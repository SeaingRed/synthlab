"""The input matrix, the mixer's row: the surface's core edit — **a level,
a pan, a send** — by each input alone, every one reaching the same server
state.

- mouse + wheel: Playwright's mouse — a fader drag, Shift = fine, a double
  click, a typed value, the wheel on a focused knob, a right click;
- keyboard only: Tab strip to strip, Left / Right inside a strip, Up / Down
  on a value, Space on M — a test that sees a pointer fails;
- pen: SYNTHETIC pointer events (`inputs.PEN`) — a tip drag, the barrel held
  = fine, the eraser end = the default, hover = the value;
- touch: TRUSTED fingers through the DevTools protocol at the Surface's
  size — sizes, a fader drag, taps, a double tap;
- deck: `synthlab.deck.handle` (the virtual deck's path) — focus mode's four
  dials, their second page, and the Mix key page.

Each also holds what every surface must do: no page error (the
fixtures), no scroll of the page under a drag, the keyboard still inside
the strip after the post, every touch target 40 px or more.

Also here: the view plays the song and its meters follow (signal on the
master analyser; a strip's meter lit from the plan, dark once muted), rule
1 (under 5 % bare background at 1440 / 1920 / 2560), the empty state's
button, Alt+M and the palette.

What none of this proves: that a pan sounds panned or a send sounds wet
(nobody listened), a real pen's pressure or barrel, a real finger on a
44 px fader, a Stream Deck (WebHID was never run against a device), MIDI.
"""

from __future__ import annotations

import pytest

from tests.browser import test_page_layouts as b2
from tests.browser.conftest import _INIT, Studio
from tests.browser.inputs import COUNT_POINTERS, PEN, Keys, Mouse, Pen, Touch
from tests.browser.test_song_playlist import use
from tests.studio.test_song_routes import song_dict

D = "window.synthlab.deck"
tiled = b2.sized  # the sized, dense, night pages rule 1 is measured on


# --- helpers ----------------------------------------------------------------------


def _wait(page, check, what: str, timeout: float = 6.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


def track(server, tid: str) -> dict:
    return next(t for t in server.state.song["tracks"] if t["id"] == tid)


def depth(server) -> int:
    return server.state.song_undo_state()["depth"]


def ctl(tid: str, name: str) -> str:
    return f'.mix-strip[data-strip="{tid}"] [name="{name}"]'


def _open(studio, server):
    """The Mix view on the fixture song: drums td, synths t1 / t2, audio ta."""
    use(server, song_dict())
    page = studio.open("mixer")
    page.wait_for_selector('.mix-strip[data-strip="t1"]')
    studio.settle()
    return page


def _inside(page, tid: str) -> bool:
    """The keyboard is still inside that strip."""
    return page.evaluate(
        "(t) => document.activeElement?.closest('.mix-strip')?.dataset.strip === t",
        tid,
    )


def _no_scroll(page) -> bool:
    return page.evaluate("() => scrollX === 0 && scrollY === 0")


def _drag(page, selector: str, dy: float, *, shift=False, steps=6):
    """A real mouse drag on a control: `dy` px up (a knob's and a fader's
    travel is 160 px, tweak.js)."""
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    if shift:
        page.keyboard.down("Shift")
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y - dy, steps=steps)
    page.mouse.up()
    if shift:
        page.keyboard.up("Shift")


# --- mouse + wheel ----------------------------------------------------------------


def test_mixer_by_mouse_a_level_a_pan_a_send(studio, server):
    page = _open(studio, server)
    m = Mouse(page)
    was = depth(server)
    # a level: the fader, dragged down 40 px of its 160
    _drag(page, ctl("t1", "gain"), -40)
    _wait(page, lambda: track(server, "t1")["gain"] < 0.9, "the fader's post")
    assert track(server, "t1")["gain"] == pytest.approx(1.0 - 1.5 * 40 / 160, abs=0.02)
    assert depth(server) == was + 1  # one drag, one undo step
    assert _no_scroll(page) and _inside(page, "t1")
    # its read-out followed the hand
    assert page.inner_text('output[for="mix-t1-gain"]') == (
        f"{round(track(server, 't1')['gain'] * 100)}%"
    )
    # Shift: the same distance moves a tenth as far
    at = track(server, "t1")["gain"]
    _drag(page, ctl("t1", "gain"), 40, shift=True)
    _wait(page, lambda: track(server, "t1")["gain"] != at, "the fine drag's post")
    assert track(server, "t1")["gain"] - at == pytest.approx(0.0375, abs=0.011)
    # a double click: the default
    page.dblclick(ctl("t1", "gain"))
    _wait(page, lambda: track(server, "t1")["gain"] == 1.0, "the reset")
    # a value typed into the read-out (a percentage, as it reads)
    page.click('output[for="mix-t1-gain"]')
    page.keyboard.type("60")
    page.keyboard.press("Enter")
    _wait(page, lambda: track(server, "t1")["gain"] == 0.6, "the typed level")
    # the meters are dim until the edited song has been measured again
    page.wait_for_selector("#view[data-mix-stale]")
    page.wait_for_selector("#view:not([data-mix-stale])", timeout=20_000)
    page.dblclick(ctl("t1", "gain"))
    _wait(page, lambda: track(server, "t1")["gain"] == 1.0, "the reset again")
    # a pan: the knob focused by a click, then the wheel (2 % a notch)
    page.click(ctl("t1", "pan"))
    m.wheel(ctl("t1", "pan"), dy=-100)
    m.wheel(ctl("t1", "pan"), dy=-100)
    _wait(page, lambda: track(server, "t1").get("pan", 0) > 0, "the wheel's post")
    assert track(server, "t1")["pan"] == pytest.approx(0.1, abs=0.051)
    assert _no_scroll(page)  # the wheel turned the knob, not the page
    # a send: the A knob dragged up makes the returns and feeds the first
    _drag(page, ctl("t1", "send.0"), 64)
    _wait(page, lambda: track(server, "t1").get("sends"), "the send's post")
    (rid,) = [r["id"] for r in server.state.song["returns"]]
    assert track(server, "t1")["sends"][rid] == pytest.approx(0.4, abs=0.02)
    page.wait_for_selector(f'.mix-return[data-return="{rid}"]')
    # mute and solo are buttons
    page.click('.mix-strip[data-strip="t2"] [data-mix="mute"]')
    _wait(page, lambda: track(server, "t2")["mute"], "the mute")
    page.wait_for_selector(
        '.mix-strip[data-strip="t2"] [data-mix="mute"][aria-pressed="true"]'
    )
    # a right click on a value: its menu — the control's own rows, then
    # its strip's. (A press within half a second of a drag opens nothing:
    # tweak.js's `busy`, so a barrel DRAG is never a menu.)
    page.wait_for_timeout(600)
    page.click(ctl("t1", "pan"), button="right")
    page.wait_for_selector("#ctx-menu:popover-open")
    rows = page.inner_text("#ctx-menu")
    for row in ("Reset to default", "Put on dial 1", "Mute", "Solo"):
        assert row in rows, rows
    page.keyboard.press("Escape")
    # the undo button takes the mute back
    page.click("#mix-undo")
    _wait(page, lambda: not track(server, "t2")["mute"], "the undo")


def test_the_bar_mutes_and_solos_the_strip_last_touched(studio, server):
    page = _open(studio, server)
    page.click(ctl("t2", "pan"))
    page.wait_for_selector('.mix-strip[data-strip="t2"][data-focus]')
    page.click("#mix-focus-solo")
    _wait(page, lambda: track(server, "t2")["solo"], "solo of the focused strip")
    assert not track(server, "t1")["solo"]
    # the strip keeps its mark across the redraw
    page.wait_for_selector('.mix-strip[data-strip="t2"][data-focus]')
    page.click("#mix-focus-mute")
    _wait(page, lambda: track(server, "t2")["mute"], "mute of the focused strip")


# --- keyboard only ----------------------------------------------------------------


def test_mixer_by_keyboard_alone(studio, server):
    studio.page.add_init_script(COUNT_POINTERS)
    page = _open(studio, server)
    k = Keys(page)
    # Tab into the board: every control is a tab stop, in the strip's order
    # (until Nov 2026 a strip was one, and ← → walked it: a pan knob's
    # arrows did not pan)
    for _ in range(60):
        k.press("Tab")
        if page.evaluate("() => !!document.activeElement?.closest('.mix-strip')"):
            break
    assert _inside(page, "td") and k.focused() == "gain"
    k.press("Tab")
    assert page.evaluate("() => document.activeElement.dataset.mix") == "mute"
    k.press("Shift+Tab")
    assert _inside(page, "td") and k.focused() == "gain"
    for _ in range(30):  # on through td's controls to the next strip
        k.press("Tab")
        if _inside(page, "t1"):
            break
    assert k.focused() == "gain"
    was = depth(server)
    # a level: Down on the fader (a step a press); one burst is one undo step
    k.press("ArrowDown", 5)
    _wait(page, lambda: track(server, "t1")["gain"] == 0.95, "the level")
    assert _inside(page, "t1") and k.focused() == "gain"  # morphed, not replaced
    assert depth(server) == was + 1
    # Tab past M and S to the pan: its Right pans right
    k.press("Tab", 3)
    assert k.focused() == "pan" and track(server, "t1")["gain"] == 0.95
    k.press("ArrowRight", 4)
    _wait(page, lambda: track(server, "t1").get("pan") == 0.2, "the pan")
    assert _inside(page, "t1") and k.focused() == "pan"
    # a send
    k.press("Tab")
    assert k.focused() == "send.0"
    k.press("ArrowUp", 10)
    _wait(page, lambda: server.state.song.get("returns"), "the returns")
    rid = server.state.song["returns"][0]["id"]
    # (a held key posts faster than the server answers: the LAST value lands)
    _wait(
        page,
        lambda: track(server, "t1").get("sends", {}).get(rid) == pytest.approx(0.1),
        "the send",
    )
    assert _inside(page, "t1") and k.focused() == "send.0"
    # Home is the range's own: the send back to 0
    k.press("Home")
    _wait(page, lambda: "sends" not in track(server, "t1"), "the send at 0")
    # M is a button: Shift+Tab back to it, Space presses it
    k.press("Shift+Tab", 3)
    assert page.evaluate("() => document.activeElement.dataset.mix") == "mute"
    k.press("Space")
    _wait(page, lambda: track(server, "t1")["mute"], "the mute")
    assert _inside(page, "t1")
    # Ctrl+Z: the song's undo
    k.press("Control+z")
    _wait(page, lambda: not track(server, "t1")["mute"], "the undo")
    assert k.pointers() == 0 and _no_scroll(page)


def test_alt_m_and_the_palette_open_the_mix_view(studio, server):
    use(server, song_dict())
    page = studio.open("perform")
    studio.settle()
    names = page.evaluate("() => window.synthlab.commands().map((c) => c.label)")
    assert "Go to Mix" in names
    page.keyboard.press("Alt+m")
    page.wait_for_selector('#view[data-view="mixer"] .mix-strip')
    studio.settle()
    names = page.evaluate("() => window.synthlab.commands().map((c) => c.label)")
    for name in ("Balance the mix", "Mute the focused strip", "Add a return"):
        assert name in names, name


# --- pen (synthetic) --------------------------------------------------------------


def test_mixer_by_pen_tip_barrel_eraser_and_hover(studio, server):
    studio.page.add_init_script(PEN)
    page = _open(studio, server)
    p = Pen(page)
    head = '.mix-strip[data-strip="t1"] legend'  # above the fader: up is more
    # the tip drags the fader down from full travel's middle: lower level
    fader = ctl("t1", "gain")
    box = page.locator(fader).bounding_box()
    top = page.locator(head).bounding_box()
    # tweak.js: up OR right is more — the stroke's rise plus its run
    px = (box["y"] + box["height"] / 2) - (top["y"] + top["height"] / 2)
    px += (top["x"] + top["width"] / 2) - (box["x"] + box["width"] / 2)
    put = "(s) => window.synthlab.tweak(document.querySelector(s), 0.6, true)"
    page.evaluate(put, fader)
    _wait(page, lambda: track(server, "t1")["gain"] == 0.6, "the start level")
    p.drag([fader, head], pressure=0.5)
    want = min(1.5, 0.6 + 1.5 * px / 160)
    _wait(page, lambda: track(server, "t1")["gain"] > 0.6, "the pen's drag")
    assert track(server, "t1")["gain"] == pytest.approx(want, abs=0.02)
    # the barrel held: fine — the same stroke moves a tenth as far
    page.evaluate(put, fader)
    _wait(page, lambda: track(server, "t1")["gain"] == 0.6, "the level again")
    p.drag([fader, head], pressure=0.5, buttons=2, button=2)
    _wait(page, lambda: track(server, "t1")["gain"] != 0.6, "the fine stroke")
    assert track(server, "t1")["gain"] - 0.6 == pytest.approx(
        1.5 * px / 160 / 10, abs=0.011
    )
    # a pan and a send, the tip on the knobs
    p.drag([ctl("t1", "pan"), fader], pressure=0.4)
    _wait(page, lambda: track(server, "t1").get("pan", 0) > 0, "the pen's pan")
    p.drag([ctl("t1", "send.0"), fader], pressure=0.4)
    _wait(page, lambda: track(server, "t1").get("sends"), "the pen's send")
    # hover: the value, before contact
    p.hover(ctl("t1", "pan"))
    assert page.get_attribute(ctl("t1", "pan"), "title") == page.inner_text(
        'output[for="mix-t1-pan"]'
    )
    # the eraser end: the default
    page.evaluate(
        "(s) => penAt(s, 'pointerdown', { button: 5, buttons: 32 })", ctl("t1", "pan")
    )
    _wait(page, lambda: "pan" not in track(server, "t1"), "the eraser's reset")
    # a tap on M
    p.tap('.mix-strip[data-strip="t1"] [data-mix="mute"]')
    _wait(page, lambda: track(server, "t1")["mute"], "the pen's mute")


# --- touch ------------------------------------------------------------------------


@pytest.fixture
def surface(chromium, server):
    """1368 x 912 at device scale 2 with a touch screen: the Surface's."""
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


def test_mixer_by_touch_sizes_a_fader_a_knob_and_a_tap(surface, server):
    page = _open(surface, server)
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    assert "pen" not in page.evaluate("() => ({...document.documentElement.dataset})")
    sizes = page.evaluate(
        """() => Object.fromEntries(Object.entries({
             fader: '.mix-strip[data-strip="t1"] .fader',
             knob: '.mix-strip[data-strip="t1"] [name="pan"]',
             send: '.mix-strip[data-strip="t1"] [name="send.0"]',
             mute: '.mix-strip[data-strip="t1"] [data-mix="mute"]',
             solo: '.mix-strip[data-strip="t1"] [data-mix="solo"]',
             play: '#mix-play', balance: '#mix-balance',
           }).map(([k, s]) => {
             const b = document.querySelector(s).getBoundingClientRect();
             return [k, [Math.round(b.width), Math.round(b.height)]]; }))"""
    )
    assert sizes["fader"][0] >= 44, sizes  # a 44 px fader
    for name, (w, h) in sizes.items():
        assert w >= 40 and h >= 40, (name, sizes)
    t = Touch(page)

    def mid(sel):
        b = page.locator(sel).bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    # a level: a finger down the fader
    x, y = mid(ctl("t1", "gain"))
    t.drag((x, y), (x, y + 48))
    _wait(page, lambda: track(server, "t1")["gain"] < 0.9, "the finger's fader")
    assert track(server, "t1")["gain"] == pytest.approx(1.0 - 1.5 * 48 / 160, abs=0.03)
    assert _no_scroll(page)  # the drag moved the fader, not the page
    # a pan and a send: a finger up a knob
    x, y = mid(ctl("t1", "pan"))
    t.drag((x, y), (x, y - 32))
    _wait(page, lambda: track(server, "t1").get("pan", 0) > 0, "the finger's pan")
    x, y = mid(ctl("t1", "send.1"))
    t.drag((x, y), (x, y - 32))
    _wait(page, lambda: track(server, "t1").get("sends"), "the finger's send")
    assert len(server.state.song["returns"]) == 2  # B: both defaults were made
    # a tap on S
    x, y = mid('.mix-strip[data-strip="t1"] [data-mix="solo"]')
    t.tap(x, y)
    _wait(page, lambda: track(server, "t1")["solo"], "the finger's solo")
    # a double tap on the fader: its default
    x, y = mid(ctl("t1", "gain"))
    t.tap(x, y)
    t.tap(x, y)
    _wait(page, lambda: track(server, "t1")["gain"] == 1.0, "the double tap")


# --- the deck ---------------------------------------------------------------------


def test_mixer_on_the_deck_focus_dials_pages_and_keys(studio, server):
    page = _open(studio, server)
    page.wait_for_function(f"() => !!{D}")
    page.evaluate(f"() => {D}.set({{ mode: 'focus' }})")
    # the strip last touched: its first four value controls are the dials
    page.focus(ctl("t1", "gain"))
    page.wait_for_function(f"() => {D}.slots()[0]?.id === 'mix-t1-gain'")
    ids = page.evaluate(f"() => {D}.slots().map((el) => el && el.id)")
    assert ids == ["mix-t1-gain", "mix-t1-pan", "mix-t1-send0", "mix-t1-send1"]
    face = page.evaluate(f"() => {D}.face().cols.map((c) => [c.name, c.value])")
    assert face[1] == ["Bass pan", "C"]  # a pan reads C / L20 / R20

    def turn(i, n):
        page.evaluate(
            f"([i, n]) => {D}.handle({{ type: 'turn', index: i, ticks: n }})", [i, n]
        )
        page.wait_for_timeout(60)

    was = depth(server)
    turn(0, -10)  # a level
    _wait(page, lambda: track(server, "t1")["gain"] < 1.0, "dial 1: the level")
    turn(1, 10)  # a pan
    _wait(page, lambda: track(server, "t1").get("pan", 0) > 0, "dial 2: the pan")
    turn(2, 20)  # a send
    _wait(page, lambda: track(server, "t1").get("sends"), "dial 3: send A")
    assert depth(server) == was + 3  # a step a dial
    # the redraw kept the strip under the dials
    page.wait_for_function(f"() => {D}.slots()[1]?.id === 'mix-t1-pan'")
    # a press let go unturned: the default
    page.evaluate(f"() => {D}.handle({{ type: 'dialdown', index: 1 }})")
    page.evaluate(f"() => {D}.handle({{ type: 'dialup', index: 1 }})")
    _wait(page, lambda: "pan" not in track(server, "t1"), "dial 2 pressed: centre")
    # the next four: duck and the EQ
    page.evaluate(f"() => {D}.page(1)")
    page.wait_for_function(f"() => {D}.slots()[0]?.id === 'mix-t1-duck'")
    assert page.evaluate(f"() => {D}.slots().map((el) => el && el.id)") == [
        "mix-t1-duck",
        "mix-t1-eqlow",
        "mix-t1-eqmid",
        "mix-t1-eqhigh",
    ]
    turn(1, -12)
    _wait(
        page,
        lambda: any(u["kind"] == "eq" for u in track(server, "t1")["fx"]),
        "dial 2 of page 2: the EQ",
    )
    # the Mix key page: bound to the page's own commands
    for _ in range(3):
        page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 7 }})")  # More
    keys = page.evaluate(f"() => {D}.face().keys.map((k) => [k.label, k.off])")
    assert [k[0] for k in keys] == [
        "Mix",
        "Balance",
        "Mute",
        "Solo",
        "Return",
        "Play",
        "Undo",
        "More",
    ]
    assert not any(off for _label, off in keys[:7])
    page.evaluate(f"() => {D}.handle({{ type: 'keydown', index: 2 }})")  # Mute
    _wait(page, lambda: track(server, "t1")["mute"], "the Mute key")


# --- the view plays, the meters follow, rule 1 ------------------------------------


def _level(page, tid: str) -> float:
    return page.evaluate(
        "(t) => parseFloat(document.querySelector(`.meter[data-meter='${t}']`)"
        ".style.getPropertyValue('--level')) || 0",
        tid,
    )


def test_the_mix_view_lights_its_meters_plays_and_a_mute_is_heard(studio, server):
    page = _open(studio, server)
    # zero setup: opening the view lights every sounding strip's meter
    _wait(page, lambda: _level(page, "t1") > 0, "the meters", timeout=20)
    assert _level(page, "td") > 0 and _level(page, "ta") == 0  # no take: dark
    # the fullest section is the one shown (the Verse plays all three)
    assert page.input_value("#mix-section") == "s2" and _level(page, "t2") > 0
    # it plays, through the song's own player
    page.click("#mix-play")
    studio.wait_sound()
    page.wait_for_selector('#mix-play[aria-pressed="true"]')
    # the section shown follows the playhead (the Intro comes first)
    _wait(page, lambda: page.input_value("#mix-section") == "s1", "the playhead")
    # a mute: its meter goes dark with the next plan, and the song plays on
    page.click('.mix-strip[data-strip="t1"] [data-mix="mute"]')
    _wait(page, lambda: _level(page, "t1") == 0, "the muted strip's meter", timeout=20)
    assert _level(page, "td") > 0
    assert page.evaluate("() => window.synthlab.songClock().playing")
    # the master meter is the live output
    _wait(
        page,
        lambda: page.evaluate(
            "() => parseFloat(document.getElementById('mix-out-l')"
            ".style.getPropertyValue('--level')) > 0"
        ),
        "the master meter",
    )
    page.keyboard.press("Space")  # Space stops, as on the Song page
    page.wait_for_selector('#mix-play[aria-pressed="false"]')
    # leaving the view stops nothing it should not: the page is quiet
    studio.nav("perform")
    studio.settle()


def _num(text: str) -> float:
    return float(text.replace("−", "-").split()[0])


def test_the_master_read_outs_agree_with_the_export_s_measure(studio, server):
    """The Mix page's live integrated loudness and sample peak (pagefill.js,
    off the master bus) against "measure" as played (the export's own BS.1770
    and true peak) on the same song, played through once: within 1 LU and
    1.5 dB (a read-out of -9.5 against a measured -14.2 on the seeded song
    is the disagreement this guards)."""
    page = _open(studio, server)
    with page.expect_response("**/export/song/loudness**", timeout=60_000):
        page.click("button[hx-get='/export/song/loudness']")
    page.wait_for_function(
        "() => /LUFS/.test(document.getElementById('song-loudness')?.textContent)"
    )
    said = page.locator("#song-loudness").inner_text()  # "-x LUFS · true peak -y dB"
    lufs, true_peak = _num(said), _num(said.split("true peak")[1])
    page.click("#mix-play")
    studio.wait_sound()
    page.wait_for_function(
        "() => !window.synthlab.songClock().playing", timeout=40_000, polling=200
    )
    page.wait_for_timeout(400)  # the read-outs' last quarter-second count
    live = _num(page.locator("#mm-int").inner_text())
    peak = _num(page.locator("#mm-pk-max").inner_text())
    print(f"\nexport {lufs} LUFS tp {true_peak} | live {live} LUFS pk {peak}")
    assert abs(live - lufs) <= 1.0, (live, said)
    assert true_peak - 1.5 <= peak <= true_peak + 0.1, (peak, said)


def test_the_empty_mix_views_button_makes_sound(studio, server):
    page = studio.open("mixer")
    page.wait_for_selector(".mix-empty .empty-start")
    page.click(".mix-empty .empty-start")
    studio.wait_sound(timeout=20)
    page.wait_for_selector('#view[data-view="song"]')
    studio.nav("mixer")
    page.wait_for_selector(".mix-strip[data-strip]")
    assert page.locator(".mix-strip[data-strip]").count() >= 4
    # the song the button started is still playing on the Mix view
    assert page.evaluate("() => window.synthlab.songClock().playing")
    page.click("#mix-play")  # ... and its play button stops it
    page.wait_for_function("() => !window.synthlab.songClock().playing")
    studio.settle()


@pytest.mark.parametrize("song", ["fixture", "starter", "returns", "empty"])
def test_rule_1_holds_on_the_mix_view(tiled, server, song):
    from fastapi.testclient import TestClient

    open_, bare = tiled
    hx = {"HX-Request": "true", "HX-Current-URL": f"{server.url}/view/mixer"}
    c = TestClient(server.app, base_url=server.url)
    if song == "starter":
        assert (
            c.post("/song/start", data={"style": "trance"}, headers=hx).status_code
            == 200
        )
    elif song != "empty":
        use(server, song_dict())
    if song == "returns":
        for slot in ("0", "1"):
            c.post("/song/mix/strip/t1", data={f"send.{slot}": "0.4"}, headers=hx)
    got = {}
    for w, h in b2.SIZES:
        page = open_("mixer", w, h)
        got[w] = round(bare(page), 1)
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        assert over[0] <= 1 and over[1] <= 1, (w, over)  # the view is the window
        if song not in ("empty",):
            assert page.locator(".mix-strip[data-strip]").count() >= 4
    print(f"\nrule 1, Mix ({song}): bare background {got} %")
    assert all(v < 5.0 for v in got.values()), got
