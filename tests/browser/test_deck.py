"""Journey: the control surface — the virtual deck, a fake Stream Deck +
behind WebHID, and MIDI learn.

Proves, in headless Chromium against a scratch studio: a dial turned on the
virtual deck moves the control it is bound to through that control's own
`input` (each assertion reads the scratch studio's state, which only the
control's own post can have written), in macro and in focus mode; the
strip's and the keys' gestures; that a `HIDDevice` stand-in installed as
`navigator.hid` receives well-formed key and strip reports carrying JPEGs
drawn by the page, and that its input reports drive the same surface; that
a learned CC does too.

Does NOT prove anything about a real Stream Deck +: no device was
attached. The stand-in speaks the bytes deckhid.js expects; whether
the device does, whether it opens while Elgato's app runs, and what the
images look like on its screens need the real device and a person.
"""

from __future__ import annotations

from dataclasses import replace

from synthlab.core import WAVETABLES

D = "window.synthlab.deck"


def _wait(page, check, what: str, timeout: float = 6.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


def _posts(page, path: str) -> list:
    seen: list = []
    page.on(
        "request",
        lambda r: r.method == "POST" and r.url.endswith(path) and seen.append(r),
    )
    return seen


def _open_deck(page):
    page.click("#deck-btn")
    page.wait_for_selector("html[data-deck] #deck-panel", state="visible")
    page.wait_for_function(
        "() => document.querySelector('#deck-keys button').textContent !== ''"
    )


def _box(page, selector: str) -> dict:
    return page.evaluate(
        """(s) => { const b = document.querySelector(s).getBoundingClientRect();
             return { x: b.x, y: b.y, w: b.width, h: b.height,
                      right: b.right, bottom: b.bottom }; }""",
        selector,
    )


def _face(page) -> dict:
    return page.evaluate(f"() => {D}.face()")


def _key(page, label: str, lit: bool = False):
    """Press the virtual key that reads `label` (the keys have three pages:
    the views and the transport, the tools, the beat — "More" turns them).
    `lit`: wait for it to be live first — a key is drawn from the page's
    commands a frame (and again 120 ms) after the edit that enables it
    lands, and a press before that is a dim key's: nothing."""
    has = (
        "(l) => [...document.querySelectorAll('#deck-keys button')]"
        ".some((b) => b.textContent === l)"
    )
    for _ in range(3):
        if page.evaluate(has, label):
            break
        first = page.locator("#deck-keys button").first.inner_text()
        page.locator("#deck-keys button", has_text="More").click()
        page.wait_for_function(
            "(was) => document.querySelector('#deck-keys button').textContent !== was",
            arg=first,
        )
    page.wait_for_function(has, arg=label)
    if lit:
        page.wait_for_function(
            "(l) => [...document.querySelectorAll('#deck-keys button')].some("
            "(b) => b.textContent === l && b.getAttribute('aria-disabled') !== 'true')",
            arg=label,
        )
    page.locator("#deck-keys button", has_text=label).first.click()


def _slot_of(page, control_id: str) -> int:
    return page.evaluate(
        f"(id) => {D}.slots().findIndex((el) => el && el.id === id)", control_id
    )


def _focus_mode(page, control_id: str) -> int:
    """Focus mode on the panel of one control: the dial that holds it."""
    _key(page, "Macros")
    page.wait_for_function(f"() => {D}.state().mode === 'focus'")
    page.focus(f"#{control_id}")
    page.wait_for_function(
        f"(id) => {D}.slots().some((el) => el && el.id === id)", arg=control_id
    )
    slot = _slot_of(page, control_id)
    # the panel is drawn a beat after the model changes: its knob mirrors the
    # control once it carries the control's name
    page.wait_for_function(
        f'([i, id]) => document.querySelector(`#deck-dials [data-dial="${{i}}"]`)'
        f".getAttribute('aria-label') === `dial ${{i + 1}}: ` + {D}.nameOf("
        "document.getElementById(id))",
        arg=[slot, control_id],
    )
    return slot


def test_a_virtual_dial_moves_a_macro_through_the_sliders_own_post(studio, server):
    server.state.macros[0]["targets"] = [{"param": "cutoff", "amt": 1.0}]
    page = studio.open("editor")
    studio.settle()
    before = server.state.patch.cutoff
    posts = _posts(page, "/fragment/macro/0")
    _open_deck(page)
    face = _face(page)
    assert face["mode"] == "macro"
    # the three unassigned macros' dials are not dead: the page's own knobs
    # fill them (otherwise the dials do nothing)
    spare = page.evaluate(f"() => {D}.slots().map((el) => el && el.id)")
    assert spare[0] == "macro-0"
    assert all(s and not s.startswith("macro-") for s in spare[1:])
    assert len(set(spare)) == 4
    # the virtual knob is a real `.knob` range that mirrors the macro slider
    dial = page.locator('#deck-dials [data-dial="0"]')
    assert dial.evaluate("(k) => k.matches('input[type=range].knob')")
    assert [dial.get_attribute(a) for a in ("min", "max", "step")] == ["0", "1", "0.01"]
    dial.focus()
    for _ in range(4):
        page.keyboard.press("ArrowUp")  # the native slider's key: one step
    _wait(page, lambda: abs(server.state.macros[0]["value"] - 0.04) < 1e-6, "the macro")
    assert server.state.patch.cutoff != before  # its target moved with it
    studio.settle()
    # four steps were not four posts: the slider's own 120 ms debounce held
    assert 1 <= len(posts) <= 2
    assert page.input_value("#macro-0") == "0.04"
    assert _face(page)["cols"][0]["value"] == "4%"
    # the knob shows what the slider shows: value and arc
    assert dial.input_value() == "0.04"
    assert dial.evaluate("(k) => k.style.getPropertyValue('--fill')") == "4%"
    # an unassigned macro's dial turns the control that fills it, never the macro
    assert not page.locator('#deck-dials [data-dial="1"]').is_disabled()
    was = page.evaluate(f"() => {D}.slots()[1].value")
    page.evaluate(f"() => {{ {D}.turn(1, 3); }}")
    page.wait_for_function(f"() => {D}.slots()[1].value !== {was!r}")
    page.wait_for_timeout(300)
    assert server.state.macros[1]["value"] == 0


def test_focus_mode_takes_the_touched_panel_in_fours(studio, server):
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    slot = _focus_mode(page, "p-cutoff")
    face = _face(page)
    assert face["mode"] == "focus" and face["pages"] >= 1
    assert face["cols"][slot]["name"] == "Cutoff"
    # every slot is a control of the Filter panel, in page order
    assert page.evaluate(
        f"() => {D}.slots().filter(Boolean)"
        ".every((el) => el.closest('fieldset').id === 'panel-filter')"
    )
    before = server.state.patch.cutoff
    posts = _posts(page, "/fragment/patch")
    dial = page.locator(f'#deck-dials [data-dial="{slot}"]')
    dial.focus()
    page.keyboard.press("ArrowUp")
    _wait(page, lambda: server.state.patch.cutoff > before, "the cutoff, up a step")
    studio.settle()
    assert len(posts) == 1
    assert (
        "Hz" in _face(page)["cols"][slot]["value"]
    )  # the row's own chip, unit and all
    # the page's undo key undoes it: it was an ordinary edit
    _key(page, "Undo", lit=True)
    _wait(page, lambda: server.state.patch.cutoff == before, "the undo")
    studio.settle()
    # a press = the control's default: the very value a double-click on the
    # slider posts (a log slider's default sits between two positions; the
    # deck's own reset used to post the snapped one, 2202 for 2200)
    default = float(page.get_attribute("#p-cutoff", "data-default"))
    dial.focus()
    for _ in range(40):
        page.keyboard.press("ArrowUp")
    _wait(page, lambda: server.state.patch.cutoff > before * 1.2, "a move to reset")
    studio.settle()
    page.keyboard.press("Enter")
    _wait(page, lambda: server.state.patch.cutoff == default, "the default")
    studio.settle()
    pressed = page.input_value("#p-cutoff")
    # ... and the double-click, on the virtual knob and on the slider itself
    for target in (f'#deck-dials [data-dial="{slot}"]', "#p-cutoff"):
        page.evaluate(f"(i) => {{ {D}.turn(i, 9); }}", slot)
        _wait(page, lambda: server.state.patch.cutoff != default, "a move to reset")
        studio.settle()
        page.dispatch_event(target, "dblclick")
        _wait(page, lambda: server.state.patch.cutoff == default, f"{target}'s reset")
        studio.settle()
        assert page.input_value("#p-cutoff") == pressed


def test_a_fast_turn_goes_further_than_a_slow_one(studio, server):
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    slot = _focus_mode(page, "p-resonance")
    read = "() => Number(document.getElementById('p-resonance').value)"
    page.evaluate(f"(i) => {{ {D}.turn(i, 1, true); }}", slot)  # prime the dial's clock
    page.wait_for_timeout(400)
    start = page.evaluate(read)
    page.evaluate(f"(i) => {{ {D}.turn(i, 1); }}", slot)
    page.wait_for_timeout(400)
    slow = page.evaluate(read) - start
    page.evaluate(f"(i) => {{ for (let k = 0; k < 2; k++) {D}.turn(i, 1); }}", slot)
    page.wait_for_timeout(400)
    fast = page.evaluate(read) - start - slow
    assert slow > 0
    # two ticks in the same millisecond: one at x1, one at x8
    assert fast > 4 * slow, (slow, fast)
    studio.settle()


def test_a_dial_steps_a_select_one_option_a_tick(studio, server):
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    sel = page.evaluate(
        "() => [...document.querySelectorAll('#patch select[id^=p-]')]"
        ".find((s) => s.offsetParent && s.options.length > 2).id"
    )
    slot = _focus_mode(page, sel)
    field = sel[2:]
    first = page.evaluate("(id) => document.getElementById(id).selectedIndex", sel)
    options = page.evaluate(
        "(id) => [...document.getElementById(id).options].map((o) => o.value)", sel
    )
    step = 1 if first < len(options) - 1 else -1
    page.locator(f'#deck-dials [data-dial="{slot}"]').focus()
    page.keyboard.press("ArrowUp" if step > 0 else "ArrowDown")
    _wait(
        page,
        lambda: str(getattr(server.state.patch, field)) == options[first + step],
        f"{field} one option on",
    )
    assert _face(page)["cols"][slot]["frac"] == (first + step) / (len(options) - 1)
    studio.settle()


_TABLES = list(WAVETABLES)


def _wavetables(studio, server, midi: bool = False):
    """Synths with oscillator 1 a wavetable on its first table: the picker
    (a radio group of drawings, `#p-wt_table`) is on the page."""
    server.state.patch = replace(
        server.state.patch, osc1="wavetable", wt_table=_TABLES[0]
    )
    if midi:
        studio.page.add_init_script(FAKE_MIDI)
    page = studio.open("editor")
    studio.settle()
    assert page.locator("#p-wt_table[role=radiogroup] .wt-thumb").count() == 12
    labels = page.evaluate(
        "() => [...document.querySelectorAll('#p-wt_table .wt-thumb')]"
        ".map((l) => l.textContent.trim())"
    )
    return page, labels


def test_a_dial_steps_the_wavetable_picker_a_table_a_tick(studio, server):
    """The wavetable picker is a radio group, not a select: a radio group
    with an id is a stepped control as a select is (tweak.js `steps`), so a
    dial in focus mode steps the tables one a tick through the chosen
    radio's own `input` — one post a step, the server's `wt_table` follows
    — and its column reads the row's name and the table's."""
    page, labels = _wavetables(studio, server)
    _open_deck(page)
    _key(page, "Macros")
    page.wait_for_function(f"() => {D}.state().mode === 'focus'")
    # touching a radio touches its group: the picker's panel is on the dials
    page.focus("#p-wt_table input:checked")
    page.wait_for_function(
        f"() => {D}.slots().some((el) => el && el.id === 'p-wt_table')"
    )
    slot = _slot_of(page, "p-wt_table")
    posts = _posts(page, "/fragment/patch")
    page.evaluate(f"(i) => {{ {D}.turn(i, 1); }}", slot)
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[1], "one table on")
    studio.settle()
    assert len(posts) == 1
    checked = page.eval_on_selector("#p-wt_table input:checked", "r => r.value")
    assert checked == _TABLES[1]
    col = _face(page)["cols"][slot]
    assert col["name"] == "Wavetable" and col["value"] == labels[1]
    assert col["frac"] == 1 / (len(_TABLES) - 1)
    # the page's undo key undoes it: an ordinary edit
    _key(page, "Undo", lit=True)
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[0], "the undo")
    studio.settle()
    # the virtual knob mirrors it (the tables' numbers): a key press is a tick
    page.wait_for_function(
        '(i) => document.querySelector(`#deck-dials [data-dial="${i}"]`)'
        ".getAttribute('aria-label') === `dial ${i + 1}: Wavetable`",
        arg=slot,
    )
    page.locator(f'#deck-dials [data-dial="{slot}"]').focus()
    page.keyboard.press("ArrowUp")
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[1], "the knob's tick")
    studio.settle()
    # a turn past either end stops at it
    page.evaluate(f"(i) => {{ {D}.turn(i, 40); }}", slot)
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[-1], "the last table")
    studio.settle()


def test_the_wavetable_picker_has_a_controls_menu_and_learns_a_cc(studio, server):
    """A right click on a table's drawing (the pen's barrel tap is one)
    opens the picker's own menu, as a select's: Learn MIDI, Put on dial N,
    no default to reset to. A learned CC sweeps the tables (absolute: 0..127
    the first table to the last), and a pinned dial turns them."""
    page, labels = _wavetables(studio, server, midi=True)
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').dataset.state === 'on'"
    )
    thumb = f'.wt-thumb:has(input[value="{_TABLES[3]}"])'
    page.locator(thumb).scroll_into_view_if_needed()

    def menu() -> list[str]:
        page.click(thumb, button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        return page.locator("#ctx-menu .menu-item").all_inner_texts()

    def pick(text: str) -> None:
        page.locator("#ctx-menu .menu-item", has_text=text).click()
        page.wait_for_selector("#ctx-menu", state="hidden")

    assert menu()[:6] == [
        "Learn MIDI",
        "Reset to default",
        "Put on dial 1",
        "Put on dial 2",
        "Put on dial 3",
        "Put on dial 4",
    ]
    assert page.locator("#ctx-menu .caps").first.inner_text().lower() == "wavetable"
    assert page.is_disabled("#ctx-menu .menu-item:has-text('Reset to default')")
    assert server.state.patch.wt_table == _TABLES[0]  # the right click picked nothing
    pick("Learn MIDI")
    assert "Learning Wavetable" in page.inner_text("#toasts .toast:not(.error)")
    page.evaluate("window.__cc(0xb0, 23, 64)")  # binds (and is not applied)
    page.wait_for_function("() => window.synthlab.midiLearn.list().length === 1")
    assert page.evaluate("() => window.synthlab.midiLearn.list()") == [
        {"ch": 0, "cc": 23, "to": "id:p-wt_table", "mode": "abs", "name": "Wavetable"}
    ]
    page.evaluate("window.__cc(0xb0, 23, 127)")
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[-1], "the last table")
    studio.settle()
    page.evaluate("window.__cc(0xb0, 23, 35)")  # 35 / 127 of the way: the fourth
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[3], "the fourth table")
    studio.settle()
    assert page.locator(f"{thumb} input:checked").count() == 1
    # Put on dial 3: that dial is the picker in macro mode too
    menu()
    pick("Put on dial 3")
    assert page.evaluate(f"() => {D}.slots()[2]?.id") == "p-wt_table"
    assert _face(page)["cols"][2]["pinned"] is True
    assert _face(page)["cols"][2]["value"] == labels[3]
    page.evaluate(f"() => {{ {D}.turn(2, -2); }}")
    _wait(page, lambda: server.state.patch.wt_table == _TABLES[1], "the pinned dial")
    studio.settle()


def test_the_strip_zooms_sets_resets_and_pages(studio, server):
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    slot = _focus_mode(page, "p-resonance")
    strip = page.locator("#deck-strip").bounding_box()
    col = strip["width"] / 4
    y = strip["y"] + strip["height"] / 2
    # a tap on a column (a real click on the canvas): that slot, zoomed
    page.mouse.click(strip["x"] + col * (slot + 0.5), y)
    page.wait_for_function(f"(i) => {D}.state().zoom === i", arg=slot)
    # zoomed, a tap sets the value where it lands: the middle of the fader
    lo, hi = (float(page.get_attribute("#p-resonance", a)) for a in ("min", "max"))
    page.evaluate(f"() => {D}.handle({{ type: 'tap', x: 60 + 370 }})")
    want = lo + (hi - lo) / 2
    _wait(page, lambda: abs(server.state.patch.resonance - want) < 0.02, "the fader")
    # a long press: back to the default
    default = float(page.get_attribute("#p-resonance", "data-default"))
    page.evaluate(f"() => {D}.handle({{ type: 'long', x: 400 }})")
    _wait(page, lambda: abs(server.state.patch.resonance - default) < 1e-6, "reset")
    # the left edge leaves the zoom
    page.evaluate(f"() => {D}.handle({{ type: 'tap', x: 20 }})")
    assert page.evaluate(f"() => {D}.state().zoom") == -1
    # a swipe left pages on (a real drag across the canvas), and wraps
    pages = _face(page)["pages"]
    assert pages > 1
    names = [c["name"] for c in _face(page)["cols"]]
    page.mouse.move(strip["x"] + strip["width"] * 0.8, y)
    page.mouse.down()
    page.mouse.move(strip["x"] + strip["width"] * 0.2, y, steps=4)
    page.mouse.up()
    page.wait_for_function(f"() => {D}.state().page === 1")
    assert [c["name"] for c in _face(page)["cols"]] != names
    for _ in range(pages - 1):
        page.evaluate(f"() => {D}.handle({{ type: 'swipe', x: 600, x2: 100 }})")
    assert page.evaluate(f"() => {D}.state().page") == 0
    # the meters: the strip becomes one read-out, a tap comes back
    _key(page, "Meters")
    assert _face(page)["meter"] is not None
    page.evaluate(f"() => {D}.handle({{ type: 'tap', x: 400 }})")
    assert _face(page)["meter"] is None
    studio.settle()


def test_keys_change_view_and_light_and_the_panel_survives_swaps(studio, server):
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    assert page.get_attribute("#deck-keys button >> nth=0", "aria-pressed") == "true"
    _key(page, "Loops")  # (the Perform page; its key is labelled "Loops")
    page.wait_for_selector('#view[data-view="perform"]')
    page.wait_for_function(
        "() => document.querySelectorAll('#deck-keys button')[1]"
        ".getAttribute('aria-pressed') === 'true'"
    )
    assert page.get_attribute("#deck-keys button >> nth=0", "aria-pressed") == "false"
    # play: the page's own transport, lit while it runs
    _key(page, "Play")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.wait_for_function(
        "() => document.querySelectorAll('#deck-keys button')[4]"
        ".getAttribute('aria-pressed') === 'true'"
    )
    _key(page, "Play")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    # off Synths there are no macros: the dials turn this page's own controls
    col = _face(page)["cols"][0]
    assert col["off"] is False and col["name"] != "Macro 1"
    # a full-body morph (a panel's Defaults) removes what the response does
    # not hold: the panel sits beside <body>, so it is the same node, still
    # docked; the rail button is put back
    _key(page, "Synths")
    page.wait_for_selector('#view[data-view="editor"]')
    page.evaluate("() => { document.getElementById('deck-panel').kept = true; }")
    page.click('[popovertarget="pp-filter"]')  # the panel's ⋯
    page.click('#panel-filter button[aria-label="reset Filter to defaults"]')
    studio.settle()
    page.wait_for_selector("#deck-btn[aria-pressed=true]")
    assert page.evaluate("() => document.getElementById('deck-panel').kept") is True
    assert page.is_visible("#deck-panel")
    assert page.evaluate("document.documentElement.dataset.deck") == "bottom"
    assert page.locator("#deck-btn").count() == 1
    # the panel adds no field to the page's posts
    assert page.locator("#deck-panel [name]").count() == 0
    # closed, it stays closed across a reload; open, open
    page.reload()
    page.wait_for_selector("html[data-deck] #deck-panel", state="visible")
    page.click("#deck-btn")
    page.reload()
    page.wait_for_selector("#deck-btn")
    assert not page.is_visible("#deck-panel")
    assert page.evaluate("'deck' in document.documentElement.dataset") is False
    studio.settle()


def _undo(page, server, back_to: float) -> None:
    """The page's own Undo (Synths' sound bar), once."""
    page.locator('button[data-cmd="Undo the last tweak"]:visible').first.click()
    _wait(page, lambda: server.state.patch.resonance == back_to, "one undo step")
    # ... and its morph has landed: the slider shows it
    _wait(
        page,
        lambda: float(page.input_value("#p-resonance")) == back_to,
        "the undone value on the slider",
    )


def _edit(page, studio, gesture) -> dict:
    """Run one gesture on #p-resonance and say what the page did with it:
    the posts it made, what the server holds, and what the row shows."""
    bodies: list = []

    def on_request(r):
        if r.method == "POST" and r.url.endswith("/fragment/patch"):
            bodies.append(r.post_data or "")

    page.on("request", on_request)
    page.evaluate(
        """() => { window.__ev = [];
             if (window.__evOn) return;
             window.__evOn = true;
             for (const t of ['input', 'change'])
               document.addEventListener(t, (e) => {
                 if (e.target.id === 'p-resonance')
                   window.__ev.push(t + ':' + e.target.value); }); }"""
    )
    gesture()
    page.wait_for_timeout(500)  # the form's 150 ms debounce, a dial's 250 ms settle
    studio.settle()
    page.remove_listener("request", on_request)
    events = page.evaluate("window.__ev")
    return {
        "posts": len(bodies),
        "posted": [
            b for b in "&".join(bodies[-1:]).split("&") if b.startswith("resonance=")
        ],
        "server": studio.server.state.patch.resonance,
        "value": page.input_value("#p-resonance"),
        "fill": page.evaluate(
            "() => document.getElementById('p-resonance')"
            ".style.getPropertyValue('--fill')"
        ),
        "chip": page.inner_text('output[for="p-resonance"]'),
        "last": events[-1] if events else "",
        "changes": sum(e.startswith("change") for e in events),
    }


def _dial_to(page, slot: int, steps: int) -> None:
    """Dial `slot`, pressed and turned `steps` ticks: one step a tick."""
    page.evaluate(
        f"([i, n]) => {{ {D}.handle({{ type: 'dialdown', index: i }});"
        f" {D}.handle({{ type: 'turn', index: i, ticks: n }});"
        f" {D}.handle({{ type: 'dialup', index: i }}); }}",
        [slot, steps],
    )


def test_a_dial_turn_is_a_drag_in_posts_read_out_and_undo(studio, server):
    """One path for every way in: a hand dragging the control and a dial
    turned to the same value make the same post, show the same read-out and
    arc, end in one `change`, and undo in one step — on a plain slider and
    on the same control drawn as a `.knob` (how secondary sliders are
    drawn)."""
    page = studio.open("editor")
    studio.settle()
    start = server.state.patch.resonance
    _open_deck(page)
    slot = _focus_mode(page, "p-resonance")
    page.evaluate(
        "() => document.getElementById('p-resonance')"
        ".scrollIntoView({ block: 'center' })"
    )
    step = float(page.get_attribute("#p-resonance", "step"))
    as_knob = (
        "() => { const el = document.getElementById('p-resonance');"
        " el.classList.add('knob'); el.scrollIntoView({ block: 'center' }); }"
    )
    for knob in (False, True):
        if knob:
            page.evaluate(as_knob)
        box = page.locator("#p-resonance").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        def drag(cx=cx, cy=cy, box=box, knob=knob):
            # a slider: from its thumb to the right; a knob: 40 px up
            x0 = cx if knob else box["x"] + box["width"] * start / 0.95
            hit = page.evaluate(
                "([x, y]) => document.elementFromPoint(x, y)?.id", [x0, cy]
            )
            assert hit == "p-resonance", (hit, box)
            page.mouse.move(x0, cy)
            page.mouse.down()
            if knob:
                page.mouse.move(x0, cy - 40, steps=4)
            else:
                page.mouse.move(x0 + 30, cy, steps=4)
            page.mouse.up()

        dragged = _edit(page, studio, drag)
        assert dragged["server"] != start and dragged["posts"] == 1, dragged
        _undo(page, server, start)
        if knob:  # (the undo's morph drew the server's plain slider again)
            page.evaluate(as_knob)
            assert page.evaluate(
                "() => getComputedStyle(document.getElementById('p-resonance')).width"
            ) in ("28px", "24px", "40px")  # comfy, THE dense dial, a finger's
        ticks = round((dragged["server"] - start) / step)
        turned = _edit(page, studio, lambda ticks=ticks: _dial_to(page, slot, ticks))
        assert turned == dragged, (knob, dragged, turned)
        assert turned["changes"] == 1 and turned["last"].startswith("change:")
        _undo(page, server, start)  # one undo step for the whole turn
        studio.settle()
    # the knob's arc is the slider's own --fill: the dial drew it too
    assert turned["fill"].endswith("%") and turned["fill"] != "0%"


def test_the_keys_are_the_pages_commands_and_dim_without_one(studio, server):
    """A key is bound to command NAMES (`data-cmd`), resolved through the
    palette's harvest: it runs the control that declares the command, is lit
    from that control, and is dim where no control declares it."""
    page = studio.open("library")
    studio.settle()
    _open_deck(page)
    keys = {k["label"]: k for k in _face(page)["keys"]}
    # Library has no transport of its own (the rail's stop is declared, but
    # hidden while nothing plays): the Play key is dim
    assert keys["Play"] == {"label": "Play", "on": False, "off": True}
    assert page.get_attribute("#deck-keys button >> nth=4", "aria-disabled") == "true"
    # (a dim key does nothing)
    page.locator("#deck-keys button", has_text="Play").dispatch_event("click")
    assert not page.evaluate("window.synthlab.isPlaying()")
    assert not keys["Synths"]["off"] and not keys["Macros"]["off"]
    # every bound name is a command the palette lists too
    names = page.evaluate("() => window.synthlab.commands().map((c) => c.label)")
    for name in ("Go to Synths", "Go to Song", "Deck dials: macros / focus"):
        assert name in names, name
    # the declaration IS the binding: take it off the control and the key dims
    page.evaluate(
        "() => document.querySelector('[data-cmd=\"Go to Song\"]')"
        ".removeAttribute('data-cmd')"
    )
    assert {k["label"]: k["off"] for k in _face(page)["keys"]}["Song"] is True
    # on Perform the same keys find that page's own commands
    _key(page, "Loops")  # (the Perform page; its key is labelled "Loops")
    page.wait_for_selector('#view[data-view="perform"]')
    studio.settle()
    keys = {k["label"]: k for k in _face(page)["keys"]}
    assert not keys["Play"]["off"] and not keys["Undo"]["off"]
    _key(page, "More")
    page.wait_for_function(
        "() => document.querySelector('#deck-keys button').textContent === 'Library'"
    )
    second = {k["label"]: k for k in _face(page)["keys"]}
    assert not second["Resample"]["off"] and not second["Click"]["off"]
    # lit from the control's own state: the metronome's button
    _key(page, "Click")
    page.wait_for_function(
        "() => document.getElementById('click-btn').getAttribute('aria-pressed')"
        " === 'true'"
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#deck-keys button')[6]"
        ".getAttribute('aria-pressed') === 'true'"
    )
    _key(page, "Click")
    # the deck's own actions are commands too, on its own buttons
    _key(page, "Meters")
    page.wait_for_function(f"() => {D}.state().view === 'meter'")
    page.wait_for_selector("#deck-meters[aria-pressed=true]")
    assert page.is_visible("#deck-meter")  # the canonical .meter, in the head
    page.click("#deck-meters")
    page.wait_for_function(f"() => {D}.state().view === 'params'")
    studio.settle()


def test_the_deck_docks_under_or_beside_the_page_never_over_it(studio, server):
    page = studio.open("editor")
    studio.settle()
    view = "() => document.getElementById('view').getBoundingClientRect()"
    wide = page.evaluate(f"{view}.width")
    _open_deck(page)
    # a strip along the bottom, beside the rail; the keys dock stands on it
    panel, keys = _box(page, "#deck-panel"), _box(page, ".keys-dock")
    assert panel["bottom"] == 900 and panel["right"] == 1400
    assert panel["x"] == _box(page, ".topbar")["right"]
    assert keys["bottom"] <= panel["y"] + 1, (keys, panel)
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    assert _box(page, "#view")["bottom"] <= panel["y"] + 1  # the page ends above it
    assert page.evaluate(f"{view}.width") == wide
    # the same components as the page: buttons, knobs, a LED, a caption
    for sel in (
        "#deck-keys > button.btn",
        "#deck-dials input[type=range].knob",
        "#deck-panel .panel-head .led",
        "#deck-panel .panel-head h2.caps",
    ):
        assert page.locator(sel).count() >= 1, sel
    assert (
        page.evaluate("() => document.getElementById('deck-panel').style.cssText") == ""
    )
    # the strip is painted from the page's tokens: it follows the theme. It
    # used to be --bg-inset itself; since the "Afterhours" look it is dark
    # glass in both themes (the device is a black slab), mixed from the
    # theme's own --bg-inset and --text, so it is dark in each and differs
    pixel = (
        "() => [...document.getElementById('deck-strip').getContext('2d')"
        ".getImageData(100, 4, 1, 1).data].join()"
    )
    seen = []
    for _ in range(3):  # the toggle reloads the page: system (= day here), day, night
        page.wait_for_function(
            "() => document.querySelector('#deck-keys button')?.textContent"
        )
        page.wait_for_timeout(200)
        seen.append(page.evaluate(pixel))
        r, g, b, a = (int(v) for v in seen[-1].split(","))
        assert a == 255 and max(r, g, b) < 70, seen[-1]  # dark glass, opaque
        with page.expect_navigation():
            page.click(".theme-toggle")
        page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    assert len(set(seen)) == 2, seen
    page.wait_for_selector("html[data-deck] #deck-panel", state="visible")
    # beside the page instead: a column at the right, and the page narrows
    page.click("#deck-side")
    assert page.evaluate("document.documentElement.dataset.deck") == "side"
    panel = _box(page, "#deck-panel")
    assert (panel["x"], panel["y"], panel["w"], panel["h"]) == (1100, 0, 300, 900)
    assert wide - page.evaluate(f"{view}.width") == 300
    assert _box(page, ".keys-dock")["right"] <= 1100
    sideways = (
        "() => document.documentElement.scrollWidth"
        " - document.documentElement.clientWidth"
    )
    assert page.evaluate(sideways) <= 0
    # where it is docked is remembered, and a view change keeps it
    page.reload()
    page.wait_for_selector('html[data-deck="side"] #deck-panel', state="visible")
    page.evaluate("() => { document.getElementById('deck-panel').kept = true; }")
    studio.nav("perform")
    studio.settle()
    assert page.evaluate("() => document.getElementById('deck-panel').kept") is True
    assert _box(page, "#deck-panel")["x"] == 1100
    assert page.evaluate(sideways) <= 0
    # too narrow for a column: it is the bottom strip again
    page.set_viewport_size({"width": 1000, "height": 800})
    panel = _box(page, "#deck-panel")
    assert panel["bottom"] == 800 and panel["w"] > 900
    page.set_viewport_size({"width": 1400, "height": 900})
    page.click("#deck-side")
    assert page.evaluate("document.documentElement.dataset.deck") == "bottom"
    studio.settle()


def test_a_controls_menu_resets_pins_a_dial_and_learns_midi(studio, server):
    """Right click (the pen's barrel tap arrives as one) on a value control
    opens its own menu, built from the design system's `.menu`."""
    server.state.macros[0]["targets"] = [{"param": "drive", "amt": 1.0}]
    studio.page.add_init_script(FAKE_MIDI)
    page = studio.open("editor")
    studio.settle()
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').dataset.state === 'on'"
    )
    page.evaluate(
        "() => document.getElementById('p-cutoff').scrollIntoView({ block: 'center' })"
    )

    def menu() -> list[str]:
        page.click("#p-cutoff", button="right")
        page.wait_for_selector("#ctx-menu:popover-open")
        return page.locator("#ctx-menu .menu-item").all_inner_texts()

    def pick(text: str) -> None:
        page.locator("#ctx-menu .menu-item", has_text=text).click()
        page.wait_for_selector("#ctx-menu", state="hidden")

    rows = menu()
    assert rows[:6] == [
        "Learn MIDI",
        "Reset to default",
        "Put on dial 1",
        "Put on dial 2",
        "Put on dial 3",
        "Put on dial 4",
    ]
    # then its panel's (the section menu: test_put_these_on_the_deck_...)
    assert rows[6].startswith("Put these on the deck")
    assert page.locator("#ctx-menu .caps").first.inner_text().lower() == "cutoff"
    assert page.locator("#ctx-menu.pop.menu").count() == 1
    # the keyboard: the first row has focus, arrows move, Esc closes
    assert page.evaluate("document.activeElement.textContent") == "Learn MIDI"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.textContent") == "Reset to default"
    page.keyboard.press("Escape")
    page.wait_for_selector("#ctx-menu", state="hidden")
    # Reset to default: the double-click's reset, exactly
    default = float(page.get_attribute("#p-cutoff", "data-default"))
    page.evaluate(
        "() => window.synthlab.tweak(document.getElementById('p-cutoff'), 0.2, true)"
    )
    _wait(page, lambda: server.state.patch.cutoff < default / 2, "a move to reset")
    studio.settle()
    menu()
    pick("Reset to default")
    _wait(page, lambda: server.state.patch.cutoff == default, "the reset")
    studio.settle()
    # Put on dial 2: that dial is the cutoff in macro mode too, and off Synths
    # the slot falls back to the mode's (an unassigned macro's: the touched
    # panel's next control, never the pinned one twice)
    menu()
    pick("Put on dial 2")
    assert page.evaluate(f"() => {D}.state().mode") == "macro"
    ids = page.evaluate(f"() => {D}.slots().map((el) => el && el.id)")
    assert ids[:2] == ["macro-0", "p-cutoff"]
    assert ids.count("p-cutoff") == 1 and all(ids[2:])
    cols = _face(page)["cols"]
    assert cols[1]["name"] == "Cutoff" and cols[1]["pinned"] is True
    before = server.state.patch.cutoff
    page.evaluate(f"() => {{ {D}.turn(1, 5); }}")
    _wait(page, lambda: server.state.patch.cutoff > before, "the pinned dial")
    studio.settle()
    page.reload()  # the pin is this browser's
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    assert page.evaluate(f"() => {D}.slots()[1]?.id") == "p-cutoff"
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').dataset.state === 'on'"
    )
    page.evaluate(
        "() => document.getElementById('p-cutoff').scrollIntoView({ block: 'center' })"
    )
    assert "Take off dial 2" in menu()
    pick("Take off dial 2")
    # off the pin, the dial is the unassigned macro's: the touched panel's
    # first control, which is the cutoff again, unpinned
    assert page.evaluate(f"() => {D}.slots()[1]?.id") not in ("macro-1", None)
    assert not _face(page)["cols"][1].get("pinned")
    # Learn MIDI: it says so with the deck shut, the next CC binds, and moves it
    menu()
    pick("Learn MIDI")
    assert "Learning Cutoff" in page.inner_text("#toasts .toast:not(.error)")
    page.evaluate("window.__cc(0xb0, 22, 64)")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#toasts .toast')]"
        ".some((t) => t.textContent === 'CC 22 · ch 1 → Cutoff')"
    )
    assert page.evaluate("() => window.synthlab.midiLearn.list()") == [
        {"ch": 0, "cc": 22, "to": "id:p-cutoff", "mode": "abs", "name": "Cutoff"}
    ]
    low = float(page.get_attribute("#p-cutoff", "data-min"))
    page.evaluate("window.__cc(0xb0, 22, 0)")
    _wait(page, lambda: abs(server.state.patch.cutoff - low) < 1, "cutoff at its floor")
    studio.settle()
    # bound, the menu offers to forget it
    assert menu()[:2] == ["Learn MIDI", "Forget MIDI"]
    pick("Forget MIDI")
    assert page.evaluate("() => window.synthlab.midiLearn.list()") == []
    page.evaluate("window.__cc(0xb0, 22, 127)")
    page.wait_for_timeout(400)
    assert abs(server.state.patch.cutoff - low) < 1
    # a select has the menu too (no default to reset to); the grids' right
    # click is still an erase and opens nothing
    sel = page.evaluate(
        "() => [...document.querySelectorAll('#patch select[id^=p-]')]"
        ".find((s) => s.offsetParent).id"
    )
    page.evaluate(
        "(id) => document.getElementById(id).scrollIntoView({ block: 'center' })", sel
    )
    page.click(f"#{sel}", button="right")
    page.wait_for_selector("#ctx-menu:popover-open")
    assert page.is_disabled("#ctx-menu .menu-item:has-text('Reset to default')")
    page.keyboard.press("Escape")
    studio.settle()


# A stand-in for the device behind WebHID. It records what the page sends
# (report id, the first 16 bytes, the length) and lets the test replay
# input reports to the page's listener.
FAKE_HID = """
(() => {
  const sent = [];
  let listener = null;
  const device = {
    vendorId: 0x0fd9, productId: 0x0084, productName: "Stream Deck +", opened: false,
    async open() {
      if (window.__hidBusy) {
        throw new DOMException("Failed to open the device.", "NotAllowedError");
      }
      this.opened = true;
    },
    async close() { this.opened = false; },
    async sendReport(id, d) {
      const jpeg = [d[7], d[8], d[15], d[16]];
      sent.push({ id, len: d.length, head: [...d.slice(0, 16)], jpeg });
    },
    async sendFeatureReport(id, d) {
      sent.push({ id, feature: [...d.slice(0, 2)] });
    },
    addEventListener(type, fn) { if (type === "inputreport") listener = fn; },
    removeEventListener() { listener = null; },
  };
  const hid = {
    async requestDevice({ filters }) {
      window.__hidFilters = filters;
      return [device];
    },
    async getDevices() { return window.__hidGranted ? [device] : []; },
    addEventListener() {},
  };
  Object.defineProperty(navigator, "hid", { value: hid, configurable: true });
  window.__hid = {
    sent, device,
    input: (bytes) => listener(
      { reportId: 1, data: new DataView(Uint8Array.from(bytes).buffer) }),
  };
})();
"""


def _out(page, kind: int) -> list[dict]:
    return page.evaluate(
        "(k) => window.__hid.sent.filter((r) => r.id === 2 && r.head[0] === k)", kind
    )


def test_a_webhid_deck_is_drawn_and_drives_the_page(studio, server):
    server.state.macros[0]["targets"] = [{"param": "cutoff", "amt": 1.0}]
    studio.page.add_init_script(FAKE_HID)
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    assert page.inner_text("#deck-status") == "virtual"
    # how to connect is Connect's tooltip, not a line cut off under it
    assert "quit the Elgato Stream Deck app" in page.evaluate(
        "() => { const c = document.getElementById('deck-connect');"
        " return c.title || c.dataset.tip; }"
    )
    assert page.inner_text("#deck-note") == ""
    page.click("#deck-connect")
    page.wait_for_function(
        "() => document.getElementById('deck-status').textContent.includes('connected')"
    )
    assert page.evaluate("window.__hidFilters") == [
        {"vendorId": 0x0FD9, "productId": 0x0084}
    ]
    # brightness first, then all eight keys and the strip's four columns
    page.wait_for_function(
        """() => { const s = window.__hid.sent.filter((r) => r.id === 2);
             const done = (k) => new Set(s
               .filter((r) => r.head[0] === k && r.head[k === 7 ? 2 : 9] === 1)
               .map((r) => r.head[1] + 256 * (k === 7 ? 0 : r.head[2]))).size;
             return done(7) === 8 && done(12) === 4; }"""
    )
    sent = page.evaluate("window.__hid.sent")
    assert sent[0] == {"id": 3, "feature": [0x08, 70]}
    keys, strip = _out(page, 0x07), _out(page, 0x0C)
    assert all(r["len"] == 1023 for r in keys + strip)
    # each first page starts a JPEG (FF D8) right after its header
    assert all(r["jpeg"][:2] == [0xFF, 0xD8] for r in keys if r["head"][5:7] == [0, 0])
    assert all(
        r["jpeg"][2:] == [0xFF, 0xD8] for r in strip if r["head"][10:12] == [0, 0]
    )
    # the columns: x = 0, 200, 400, 600; 200 x 100 each
    rects = {tuple(r["head"][1:9]) for r in strip}
    assert rects == {
        (x & 0xFF, x >> 8, 0, 0, 200, 0, 100, 0) for x in (0, 200, 400, 600)
    }
    # the device turns dial 1 four ticks, pressed (fine): the macro moves...
    page.evaluate("window.__hid.sent.length = 0")
    page.evaluate("window.__hid.input([0x03, 5, 0, 0x00, 1, 0, 0, 0])")
    page.evaluate("window.__hid.input([0x03, 5, 0, 0x01, 4, 0, 0, 0])")
    page.evaluate("window.__hid.input([0x03, 5, 0, 0x00, 0, 0, 0, 0])")
    _wait(page, lambda: abs(server.state.macros[0]["value"] - 0.04) < 1e-6, "the macro")
    # ...the press was a press + turn, so it did not reset anything
    assert server.state.patch.cutoff > 0
    # ...and only the column that changed is drawn again
    page.wait_for_function("() => window.__hid.sent.some((r) => r.head?.[0] === 12)")
    page.wait_for_timeout(400)
    again = {r["head"][1] + 256 * r["head"][2] for r in _out(page, 0x0C)}
    assert again == {0}
    assert _out(page, 0x07) == []
    # a key on the device: Perform (key 2), and its light follows
    page.evaluate("window.__hid.input([0x00, 8, 0, 0, 1, 0, 0, 0, 0, 0, 0])")
    page.wait_for_selector('#view[data-view="perform"]')
    page.wait_for_function(
        "() => new Set(window.__hid.sent.filter((r) => r.head?.[0] === 7)"
        ".map((r) => r.head[1])).has(1)"
    )
    # garbage from the device changes nothing and breaks nothing
    for junk in ([], [0x03], [0x02, 0, 0, 3, 0, 1], [0xEE, 1, 2, 3, 4, 5, 6, 7, 8]):
        page.evaluate("(b) => window.__hid.input(b)", junk)
    assert page.evaluate(f"() => {D}.state().zoom") == -1
    # Disconnect hands the device back to its logo and closes it
    page.click("#deck-connect")
    page.wait_for_function("() => !window.__hid.device.opened")
    assert page.evaluate("window.__hid.sent.at(-1)") == {"id": 3, "feature": [0x02, 0]}
    assert page.inner_text("#deck-status") == "virtual"
    studio.settle()


def test_a_deck_that_will_not_open_says_to_quit_the_elgato_app(studio, server):
    studio.page.add_init_script(FAKE_HID + "window.__hidBusy = true;")
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    page.click("#deck-connect")
    toast = page.wait_for_selector("#toasts .toast")
    text = toast.inner_text()
    assert "Failed to open the device" in text
    assert "quit the Elgato Stream Deck app" in text
    assert page.inner_text("#deck-status") == "virtual"
    studio.settle()


FAKE_MIDI = """
(() => {
  const input = {
    id: "fake", name: "Fake knobs", type: "input", state: "connected",
    onmidimessage: null,
  };
  const access = { inputs: new Map([["fake", input]]), onstatechange: null };
  navigator.requestMIDIAccess = async () => access;
  window.__cc = (...data) => input.onmidimessage({ data, target: input });
})();
"""


def test_midi_learn_binds_a_cc_to_a_dial_and_to_a_control(studio, server):
    server.state.macros[0]["targets"] = [{"param": "resonance", "amt": 1.0}]
    studio.page.add_init_script(FAKE_MIDI)
    page = studio.open("editor")
    studio.settle()
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').dataset.state === 'on'"
    )
    _open_deck(page)
    # learn dial 1; a reserved CC is refused and learning goes on
    page.click('#deck-dials [data-learn="slot:0"]')
    assert "Learning dial 1" in page.inner_text("#deck-note")
    page.evaluate("window.__cc(0xb0, 64, 127)")
    page.wait_for_selector("#toasts .toast.error")
    assert page.locator("#deck-map li").count() == 0
    page.evaluate("window.__cc(0xb0, 64, 0)")  # (the pedal still comes up)
    page.evaluate("window.__cc(0xb0, 21, 0)")
    page.wait_for_selector("#deck-map li")
    assert "CC 21 · ch 1 → dial 1" in page.inner_text("#deck-map li")
    # absolute: 0..127 is the macro's travel
    page.evaluate("window.__cc(0xb0, 21, 127)")
    _wait(page, lambda: server.state.macros[0]["value"] == 1.0, "the macro at the top")
    studio.settle()
    # relative (two's complement): 127 is one tick down = 1%
    page.click("#deck-map [data-cycle]")
    assert page.inner_text("#deck-map [data-cycle]") == "rel2c"
    page.evaluate("window.__cc(0xb0, 21, 127)")
    _wait(
        page,
        lambda: abs(server.state.macros[0]["value"] - 0.99) < 1e-6,
        "one tick down",
    )
    studio.settle()
    # learn the last control touched: a second CC onto the cutoff itself
    page.focus("#p-cutoff")
    page.wait_for_function(
        "() => document.getElementById('deck-learn-last').textContent"
        " === 'learn: Cutoff'"
    )
    page.click("#deck-learn-last")
    page.evaluate("window.__cc(0xb1, 22, 0)")
    page.wait_for_function(
        "() => document.querySelectorAll('#deck-map li').length === 2"
    )
    page.evaluate("window.__cc(0xb1, 22, 0)")  # (the CC that binds is not applied)
    low = float(page.get_attribute("#p-cutoff", "data-min"))
    _wait(page, lambda: abs(server.state.patch.cutoff - low) < 1, "cutoff at its floor")
    # the map is this browser's: it is there after a reload, and can be forgotten
    studio.settle()
    page.reload()
    page.wait_for_selector("#deck-btn")
    page.wait_for_selector("html[data-deck] #deck-panel")  # it was open: it still is
    assert page.locator("#deck-map li").count() == 2
    page.click('#deck-map [data-unlearn="0"]')
    page.wait_for_function(
        "() => document.querySelectorAll('#deck-map li').length === 1"
    )
    assert "Cutoff" in page.inner_text("#deck-map li")
    studio.settle()


# a key's face and the strip as pixels: the canvas a key shows, as luminance
# boxes (the centre, a corner) and how much it varies
_LOOK = """(sel) => {
  const c = document.querySelector(sel), g = c.getContext('2d');
  const lum = (x, y, w, h) => { const d = g.getImageData(x, y, w, h).data; let s = 0;
    for (let i = 0; i < d.length; i += 4) s += (0.2126 * d[i] + 0.7152 * d[i + 1]
      + 0.0722 * d[i + 2]) * d[i + 3] / 255;
    return s / (d.length / 4); };
  const d = g.getImageData(0, 0, c.width, c.height).data, seen = new Set();
  for (let i = 0; i < d.length; i += 16)
    seen.add(d[i] >> 3 << 10 | d[i + 1] >> 3 << 5 | d[i + 2] >> 3);
  const w = c.width, h = c.height;
  const b = Math.max(2, Math.round(Math.min(w, h) * 0.2));
  return { w, h, centre: lum((w - b) >> 1, (h - b) >> 1, b, b), corner: lum(0, 0, 3, 3),
           colours: seen.size };
}"""


def test_the_deck_is_drawn_lit_and_still_at_rest_in_both_themes(studio, server):
    """The "Afterhours" look (deckart.js): every key face and the strip are
    drawn — not blank, not flat — in the day and the night theme without a
    console error (the harness fails on one), a lit key's centre glows over
    its corner, and nothing is drawn again while nothing changes: the
    drawing runs on change, and on a frame clock only while something moves.
    It proves pixels exist and where the light is; how the glass reads on
    the device's panels needs the real device and a person."""
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    for theme in range(2):
        page.wait_for_function(
            "() => document.querySelector('#deck-keys button')?.textContent"
        )
        page.wait_for_timeout(300)
        keys = page.locator("#deck-keys canvas")
        assert keys.count() == 8
        for i in range(8):
            look = page.evaluate(_LOOK, f'#deck-keys [data-key="{i}"] canvas')
            assert look["w"] > 20 and look["h"] > 20, look  # sized to the button
            assert look["colours"] > 12, (i, look)  # a gradient and a face, not flat
            assert look["corner"] < 1, look  # the rounded key: its corner is clear
        # key 1 is Synths, lit on Synths: the bloom and the label make its
        # centre far brighter than its rim
        lit = page.evaluate(_LOOK, '#deck-keys [data-key="0"] canvas')
        assert (
            page.get_attribute("#deck-keys button >> nth=0", "aria-pressed") == "true"
        )
        assert lit["centre"] > 40, lit
        strip = page.evaluate(_LOOK, "#deck-strip")
        # the strip: dark glass (its lit top edge included) with text and traces on it
        assert strip["colours"] > 12 and strip["corner"] < 80, strip
        # at rest, nothing is drawn again
        page.wait_for_timeout(600)
        before = page.evaluate("() => window.synthlab.deckArt.draws")
        page.wait_for_timeout(1000)
        assert page.evaluate("() => window.synthlab.deckArt.draws") == before
        # a move is drawn, with its afterglow, and then it is still again
        page.evaluate(
            f"() => {D}.setEl(document.getElementById('macro-0')"
            " || document.querySelector('#patch input[type=range]'), 0.9)"
        )
        page.wait_for_timeout(1500)
        settled = page.evaluate("() => window.synthlab.deckArt.draws")
        page.wait_for_timeout(1000)
        assert page.evaluate("() => window.synthlab.deckArt.draws") == settled
        if theme == 0:
            with page.expect_navigation():
                page.click(".theme-toggle")
            page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
            page.wait_for_selector("html[data-deck] #deck-panel", state="visible")
    studio.settle()


# the mean colour of a key's centre box (r, g, b), where its label glows
_TINT = """(sel) => {
  const c = document.querySelector(sel), g = c.getContext('2d');
  const b = Math.max(2, Math.round(Math.min(c.width, c.height) * 0.4));
  const d = g.getImageData((c.width - b) >> 1, (c.height - b) >> 1, b, b).data;
  const s = [0, 0, 0];
  for (let i = 0; i < d.length; i += 4) for (let k = 0; k < 3; k++) s[k] += d[i + k];
  return s.map((x) => x / (d.length / 4));
}"""


def test_both_decks_follow_a_hue_set_on_the_page(studio, server):
    """The colour system (style.css section 1): a hue is a variable on
    <html>. Set `--hue-accent` there and say so — what the Colours popover does — and
    the virtual deck's accent key (Synths, key 1: lit, in the accent) is
    drawn again in the new hue, with no theme switch and no reload; the
    Stream Deck + (the WebHID stand-in) is sent every key image again. A
    colour the deck caches is read again when a hue moves, not only when
    the theme does."""
    studio.page.add_init_script(FAKE_HID)
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    page.click("#deck-connect")
    page.wait_for_function(
        "() => document.getElementById('deck-status').textContent.includes('connected')"
    )
    page.wait_for_function(
        "() => new Set(window.__hid.sent.filter((r) => r.id === 2 && r.head[0] === 7)"
        ".map((r) => r.head[1])).size === 8"
    )
    page.wait_for_timeout(400)
    key = '#deck-keys [data-key="0"] canvas'
    amber = page.evaluate(_TINT, key)
    assert amber[0] > amber[2] + 20, amber  # the night's amber: red over blue
    page.evaluate("window.__hid.sent.length = 0")
    # (as colours.js does: the hue, then `synthlab-colours` — the decks do
    # not watch <html>'s style, which moves on every --keys-h)
    page.evaluate(
        "() => { document.documentElement.style.setProperty('--hue-accent', '250');"
        " document.dispatchEvent(new CustomEvent('synthlab-colours')); }"
    )
    page.wait_for_function(
        f"(sel) => {{ const t = ({_TINT})(sel); return t[2] > t[0] + 20; }}", arg=key
    )
    blue = page.evaluate(_TINT, key)
    assert blue[2] > blue[0] + 20, (amber, blue)  # the same key, now blue
    # the device: every key's image again (a new ink clears what was sent)
    page.wait_for_function(
        "() => new Set(window.__hid.sent.filter((r) => r.id === 2 && r.head[0] === 7)"
        ".map((r) => r.head[1])).size === 8"
    )
    # and back: lifting the hue redraws the amber
    page.evaluate(
        "() => { document.documentElement.style.removeProperty('--hue-accent');"
        " document.dispatchEvent(new CustomEvent('synthlab-colours')); }"
    )
    page.wait_for_function(
        f"(sel) => {{ const t = ({_TINT})(sel); return t[0] > t[2] + 20; }}", arg=key
    )
    studio.settle()


def test_a_pressed_key_flashes_and_a_page_turn_redraws_only_the_keys(studio, server):
    """A press lights the key for a moment and sends a ring across it (its
    face is drawn a few times, then stops); "More" turns the page: the eight
    keys are drawn again. The count is bounded (measured: 25 — eight faces,
    the flash's and the ring's frames on the pressed key, drawn on every
    frame, not held to 30 a second; the strip's
    columns did not change and are not drawn) and then still."""
    page = studio.open("library")
    studio.settle()
    _open_deck(page)
    page.wait_for_timeout(500)
    draws = "() => window.synthlab.deckArt.draws"
    before = page.evaluate(draws)
    page.locator("#deck-keys button", has_text="More").click()
    page.wait_for_function(
        "() => document.querySelector('#deck-keys button').textContent === 'Library'"
    )
    page.wait_for_timeout(800)
    moved = page.evaluate(draws) - before
    # the eight new faces, the flash's and the ring's frames on the pressed key
    # (the ring is 12 steps, the flash 8: they overlap)
    assert 9 <= moved <= 8 + 24, moved
    page.wait_for_timeout(600)
    assert page.evaluate(draws) == before + moved
    studio.settle()


# a value set on dial 1's control -> how many animation frames, and how many
# ms, until the virtual deck draws it (the face is coalesced through rAF)
_TO_FRAME = """(v) => new Promise((done) => {
  const A = window.synthlab.deckArt, was = A.draws, t0 = performance.now();
  let frames = 0;
  window.synthlab.deck.setEl(document.getElementById('macro-0'), v);
  const look = () => {
    frames++;
    if (A.draws !== was) done({ frames, ms: performance.now() - t0 });
    else if (frames > 30) done({ frames, ms: null });
    else requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
})"""


def test_a_dial_change_is_drawn_on_the_next_frame_and_nothing_at_rest(studio, server):
    """The deck answers a change on the next animation frame (it waited
    ~90 ms on two timers and drew at most 30 times a second: measured
    34-47 ms median from a dial's value to its first draw, now 20-21 ms —
    a frame and its rAF). Still at rest once the afterglow and the roll end."""
    server.state.macros[0]["targets"] = [{"param": "cutoff", "amt": 1.0}]
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    page.wait_for_timeout(500)
    seen = []
    for n in range(8):
        page.wait_for_timeout(150)
        seen.append(page.evaluate(_TO_FRAME, 0.2 + 0.07 * n))
    # the frame after the change (one frame of slack for the one in flight)
    assert all(s["frames"] <= 2 for s in seen), seen
    assert all(s["ms"] is not None and s["ms"] < 120 for s in seen), seen
    page.wait_for_timeout(1200)  # the afterglow, the roll, the sparks: over
    still = page.evaluate("() => window.synthlab.deckArt.draws")
    assert page.evaluate("() => window.synthlab.deckArt.busy()") is False
    page.wait_for_timeout(1000)
    assert page.evaluate("() => window.synthlab.deckArt.draws") == still
    studio.settle()


def test_a_playing_deck_moves_every_frame_and_is_still_under_calm_motion(
    studio, server
):
    """While the transport runs the strip is drawn a frame at a time (the
    spectrum from the deck's own tap, the beat line, the kick), the master
    analyser motion.js draws from keeps its settings, and when it stops the
    deck comes to rest — with the OS asking for reduced motion, which the
    studio does not follow. Under Motion: calm (the Colours
    popover) a playing deck draws nothing on its own."""
    studio.page.emulate_media(reduced_motion="reduce")
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    draws = "() => window.synthlab.deckArt.draws"
    _key(page, "Play")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.wait_for_timeout(600)
    a = page.evaluate(draws)
    page.wait_for_timeout(1000)
    moved = page.evaluate(draws) - a
    assert moved >= 30, moved  # four columns a frame, less the frames that held still
    master = page.evaluate(
        "() => { const an = window.synthlab.getAnalyser();"
        " return [an.fftSize, an.smoothingTimeConstant]; }"
    )
    assert master == [2048, 0.8]
    _key(page, "Play")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    page.wait_for_timeout(1500)
    rest = page.evaluate(draws)
    page.wait_for_timeout(800)
    assert page.evaluate(draws) == rest
    # calm motion: no spectrum, beat or pump; the Play key lights and that is all
    page.click("#colours-btn")
    page.wait_for_selector("#colours-pop:popover-open")
    page.click('#colours-pop [data-set-motion="calm"]')
    page.keyboard.press("Escape")
    _key(page, "Play")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.wait_for_timeout(700)
    a = page.evaluate(draws)
    page.wait_for_timeout(1000)
    assert page.evaluate(draws) - a <= 2
    _key(page, "Play")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    studio.settle()


# the device's images, timed: each report the fake device takes is stamped
_STAMPED = """() => {
  const d = window.__hid.device, send = d.sendReport.bind(d);
  d.sendReport = async (id, p) => { const r = await send(id, p);
    window.__hid.sent.at(-1).t = performance.now(); return r; };
}"""


def test_the_device_gets_a_change_first_and_motion_at_a_bounded_rate(studio, server):
    """The pump: while a loop plays, motion goes to the device
    at a bounded rate (a strip region at most 20 a second, a key 15; it was
    held to 4), a dial's new value reaches it at once — drawn on the next
    frame and sent ahead of any motion waiting for the link (it waited for
    the motion queue: 160 ms median, 400 ms worst, measured; now 21 / 25) — and when the
    transport stops, the last frames go and the device is left alone."""
    server.state.macros[0]["targets"] = [{"param": "cutoff", "amt": 1.0}]
    studio.page.add_init_script(FAKE_HID)
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    page.evaluate(_STAMPED)
    page.click("#deck-connect")
    page.wait_for_function(
        "() => document.getElementById('deck-status').textContent.includes('connected')"
    )
    _key(page, "Play")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.wait_for_timeout(800)
    page.evaluate("window.__hid.sent.length = 0")
    page.wait_for_timeout(2000)
    done = page.evaluate(
        """() => window.__hid.sent.filter((r) => r.id === 2 && r.head[0] === 12
             && r.head[9] === 1).map((r) => r.head[1] + 256 * r.head[2])"""
    )
    per_region = {x: done.count(x) / 2 for x in set(done)}
    assert per_region and max(per_region.values()) <= 21, per_region
    assert min(per_region.values()) >= 3, per_region  # the motion does flow
    keys = page.evaluate(
        """() => window.__hid.sent.filter((r) => r.id === 2 && r.head[0] === 7
             && r.head[2] === 1).map((r) => r.head[1])"""
    )
    assert all(keys.count(k) / 2 <= 16 for k in set(keys)), keys
    # a change: dial 1's column, the 81% it now reads, out within 150 ms
    ms = page.evaluate(
        """() => new Promise((done) => {
          const S = window.synthlab, A = S.deckArt, regions = A.regions;
          const t0 = performance.now();
          let drawn = 0;
          A.regions = (...a) => regions(...a).map((r) => ({ ...r, draw: (g, dev) => {
            const mine = dev && r.x === 0 && r.sig.includes('"81%"');
            if (mine && !drawn) drawn = performance.now();
            return r.draw(g, dev); } }));
          S.deck.setEl(document.getElementById('macro-0'), 0.81);
          const look = () => {
            const end = drawn && window.__hid.sent.find((r) => r.t >= drawn
              && r.id === 2 && r.head[0] === 12 && r.head[1] === 0
              && r.head[2] === 0 && r.head[9] === 1);
            if (end) { A.regions = regions; done(end.t - t0); }
            else if (performance.now() - t0 > 2000) {
              A.regions = regions; done(null); }
            else setTimeout(look, 2);
          };
          look();
        })"""
    )
    assert ms is not None and ms < 150, ms
    _key(page, "Play")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    page.wait_for_timeout(1500)
    quiet = page.evaluate("window.__hid.sent.length")
    page.wait_for_timeout(1000)
    assert page.evaluate("window.__hid.sent.length") == quiet
    page.click("#deck-connect")
    page.wait_for_function("() => !window.__hid.device.opened")
    studio.settle()


def test_a_dial_set_puts_four_controls_on_the_dials_in_one_click(studio, server):
    """decksets.js: under the dials, named fours (and the page's own most
    used four): one click pins all four, the lit set again unpins them, ▶
    steps through the sets; the device's "Sets ▶" key is the same command."""
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    names = page.locator("#deck-sets [data-set]").all_inner_texts()
    assert {"Filter", "Amp env", "Oscs"} <= set(names)
    assert "★ Yours" not in names  # nothing learned yet
    page.click("#deck-sets [data-set]:text-is('Amp env')")
    ids = ["p-attack", "p-decay", "p-sustain", "p-release"]
    page.wait_for_function(
        f"() => {D}.slots().map((el) => el && el.id).join() === {','.join(ids)!r}"
    )
    lit = page.locator("#deck-sets [data-set]:text-is('Amp env')")
    page.wait_for_function(
        "() => !!document.querySelector('#deck-sets [aria-pressed=true]')"
    )
    assert lit.get_attribute("aria-pressed") == "true"
    assert all(c.get("pinned") for c in _face(page)["cols"])
    # the device's dial 2 now turns the decay
    before = server.state.patch.decay
    page.evaluate(f"() => {{ {D}.turn(1, 5); }}")
    _wait(page, lambda: server.state.patch.decay > before, "the decay")
    studio.settle()
    # a turn's `change` (tweak.later's, 250 ms on) is the deck's own: ★ Yours
    # learns from the hand, not from the dials it would set
    page.wait_for_timeout(400)
    used = page.evaluate(
        "() => JSON.parse(localStorage.getItem('synthlab.deck.used') || '{}')"
    )
    assert not [k for counts in used.values() for k in counts if "decay" in k]
    # the lit set again: unpinned, back to the mode's own
    page.click("#deck-sets [data-set]:text-is('Amp env')")
    page.wait_for_function(f"() => {D}.state().pins.every((p) => !p)")
    # ▶ steps to a set; the device key's command is on the page
    page.click("#deck-set-next")
    page.wait_for_function(f"() => {D}.state().pins.every((p) => p)")
    assert page.locator("[data-cmd='Deck dials: next set']").count() == 1
    # learned: four controls changed on this page are "★ Yours"
    for pid in ("p-cutoff", "p-attack", "p-glide", "p-noise_level"):
        page.evaluate(
            "(id) => document.getElementById(id)"
            ".dispatchEvent(new Event('change', { bubbles: true }))",
            pid,
        )
    page.wait_for_selector("#deck-sets [data-set]:text-is('★ Yours')")


def test_the_keys_set_puts_the_docks_strum_on_a_dial(studio):
    """The keyboard dock's own controls — strum, bend range, room, volume —
    are no page's dials (`pageList` skips the dock): the Keys set pins them,
    and a turn of dial 1 moves the strum (kept per browser, chords.js)."""
    page = studio.open("editor")
    studio.settle()
    _open_deck(page)
    page.click("#deck-sets [data-set]:text-is('Keys')")
    ids = ["kbd-strum", "bend-range", "space", "volume"]
    page.wait_for_function(
        f"() => {D}.slots().map((el) => el && el.id).join() === {','.join(ids)!r}"
    )
    assert page.input_value("#kbd-strum") == "0"
    page.evaluate(f"() => {{ {D}.turn(0, 4); }}")
    page.wait_for_function("() => +document.getElementById('kbd-strum').value > 0")
    page.wait_for_function("() => +localStorage.getItem('synthlab.strum') > 0")


def _section_row(page, target: str, row: str) -> None:
    """Right-click `target` (a section's background) and run its menu's `row`."""
    page.locator(target).first.scroll_into_view_if_needed()
    page.locator(target).first.click(button="right")
    page.wait_for_selector("#ctx-menu:popover-open")
    page.locator("#ctx-menu .menu-item", has_text=row).first.click()
    page.wait_for_selector("#ctx-menu", state="hidden")


_IDS = f"() => {D}.slots().map((el) => el && el.id)"


def test_put_these_on_the_deck_from_an_fx_unit_and_a_synths_panel(studio, server):
    """A section's menu (menu.js) puts its dials on the deck: an FX unit's
    knobs land on the dials (every one is `name="value"`: their ids are what
    a binding names), a dial turns the unit's parameter through its own post,
    the toast says what landed where; a Synths panel of nine pages in fours
    (Page ▶); "Add to ★ Yours" makes them the page's set, and "MIDI learn
    these" walks them, one CC each. The virtual deck shows the same model the
    Stream Deck + is drawn from (deck.js `face`)."""
    studio.page.add_init_script(FAKE_MIDI)
    page = studio.open("editor")
    studio.settle()
    with page.expect_response("**/fragment/fx/add"):
        page.evaluate(
            "() => htmx.ajax('POST', '/fragment/fx/add', {values: {scope: 'layer',"
            " kind: 'reverb'}, target: '#fx-rack', swap: 'outerHTML'})"
        )
    studio.settle()
    knobs = page.evaluate(
        "() => [...document.querySelectorAll('#fx-rack .fx-unit .fx-knobs"
        " :is(input[type=range], select)')].map((el) => el.id)"
    )
    assert knobs and all(k.startswith("fx-layer-0-") for k in knobs), knobs
    _open_deck(page)
    _section_row(page, "#fx-rack .fx-unit header strong", "Put these on the deck")
    # (a reverb has three: the fourth dial stays the mode's own)
    page.wait_for_function(
        f"(ids) => ({_IDS})().slice(0, ids.length).join() === ids.join()",
        arg=knobs[:4],
    )
    assert page.evaluate(f"() => {D}.state().pins") == [
        f"id:{k}" for k in knobs[:4]
    ] + [""] * (4 - len(knobs[:4]))
    toast = page.locator("#toasts .toast:not(.error)").last.inner_text()
    assert toast.startswith("Reverb") and "on dials 1-" in toast, toast
    assert all(c.get("pinned") for c in _face(page)["cols"][: len(knobs[:4])])
    # the virtual deck's first dial mirrors the unit's first knob
    page.wait_for_function(
        "(id) => document.querySelector('#deck-dials [data-dial=\"0\"]')"
        f".getAttribute('aria-label') === 'dial 1: ' + {D}.nameOf("
        "document.getElementById(id))",
        arg=knobs[0],
    )
    param = knobs[0].removeprefix("fx-layer-0-")
    fx = server.state.layers[server.state.active_layer]["fx"]
    before = fx[0]["params"][param]
    page.evaluate(f"() => {{ {D}.turn(0, 8); }}")
    _wait(
        page,
        lambda: (
            server.state.layers[server.state.active_layer]["fx"][0]["params"][param]
            != before
        ),
        "the unit's parameter",
    )
    studio.settle()
    # a Synths panel of nine: the first four, then Page ▶ the next four (its
    # filter type's pills among them: a radio group is a stepped control)
    filt = page.evaluate(
        "() => [...document.querySelectorAll('#panel-filter :is(input[type=range],"
        " select, [role=radiogroup][id])')].filter((el) => el.offsetParent"
        " && !el.disabled).map((el) => el.id)"
    )
    assert "p-filter_type" in filt, filt
    assert len(filt) == 10, filt  # the type, eight sliders and knobs, filter 2 (off)
    _section_row(page, "#panel-filter .panel-name", "Put these on the deck")
    page.wait_for_function(f"(ids) => ({_IDS})().join() === ids.join()", arg=filt[:4])
    assert "page 1 of 3" in page.locator("#toasts .toast:not(.error)").last.inner_text()
    page.click("#deck-next")
    page.wait_for_function(
        f"(ids) => ({_IDS})().slice(0, ids.length).join() === ids.join()",
        arg=filt[4:8],
    )
    assert _face(page)["page"] == 1 and _face(page)["pages"] == 3
    # ★ Yours: the panel's first four, kept for this page
    _section_row(page, "#panel-filter .panel-name", "Add to ★ Yours")
    page.wait_for_selector("#deck-sets [data-set]:text-is('★ Yours')")
    kept = page.evaluate(
        "() => JSON.parse(localStorage.getItem('synthlab.deck.yours'))"
    )
    assert kept == {"editor": [f"id:{i}" for i in filt[:4]]}
    page.click("#deck-sets [data-set]:text-is('★ Yours')")
    page.wait_for_function(f"(ids) => ({_IDS})().join() === ids.join()", arg=filt[:4])
    # MIDI learn these: each CC binds the next of the unit's knobs
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').dataset.state === 'on'"
    )
    _section_row(page, "#fx-rack .fx-unit header strong", "MIDI learn these")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#toasts .toast')]"
        ".some((t) => t.textContent.includes('(1 of '))"
    )
    page.evaluate("window.__cc(0xb0, 40, 0)")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#toasts .toast')]"
        ".some((t) => t.textContent.includes('(2 of '))"
    )
    page.evaluate("window.__cc(0xb0, 41, 0)")
    page.wait_for_function("() => window.synthlab.midiLearn.list().length === 2")
    bound = page.evaluate(
        "() => window.synthlab.midiLearn.list().map((b) => [b.cc, b.to])"
    )
    assert bound == [[40, f"id:{knobs[0]}"], [41, f"id:{knobs[1]}"]]
    # still walking: the menu offers to stop
    _section_row(page, "#fx-rack .fx-unit header strong", "Stop learning MIDI")
    assert page.evaluate("() => window.synthlab.midiLearn.armed()") is None
    studio.settle()
