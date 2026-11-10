"""The input matrix, the jam's row: the surface's core edit — **launch a cell,
stop it**, then capture what was played — by each input alone, every one
reaching the same server state (the song gains the section that was
launched, in one undo step).

- mouse: Playwright's mouse — the Jam switch, a pad, a second pad on the
  bar, a scene's head, a row's stop, Stop all, Capture, Undo;
- keyboard only: Alt+J, the arrows on the grid's roving focus, Enter and
  Space, Shift+Enter (the scene), Backspace (the track), Escape and Alt+0
  (all), Alt+1 (a scene), Alt+C — a test that sees a pointer fails;
- pen: SYNTHETIC pointer events (`inputs.PEN`) — taps; a pad says what it
  is on hover (its title);
- touch: TRUSTED fingers through the DevTools protocol at the Surface's
  size — every target 40 px or more, taps;
- deck: `synthlab.deck.handle` (the virtual deck's path) — the sixth key
  page: the launcher, scenes 1-4 (lit while sounding), stop all, capture.

Also here: a launch lands on a bar line of the jam's own clock (the
source's scheduled start, to a microsecond — "level steps within 30 ms"
needs an ear or a scope, see below), the countdown, the
row's meter, the song stopping when a jam starts and the jam when the song
does, the mode and the quantise surviving a reload, an edit redrawing the
launcher under what sounds, an empty song's starter sheet making sound
with Jam on, the palette's commands, and rule 1 (under 5 % bare
background at 1440 / 1920 / 2560).

What none of this proves: that a launch is heard on the bar (the start
time handed to Web Audio is; nobody listened, and no test reads the
output's samples against the clock), that a switch does not click, a real
pen or finger, a Stream Deck (WebHID was never run against a device).
"""

from __future__ import annotations

import pytest

from tests.browser import test_page_layouts as b2
from tests.browser.conftest import _INIT, Studio
from tests.browser.inputs import COUNT_POINTERS, PEN, Keys, Pen, Touch
from tests.browser.test_song_playlist import use
from tests.studio.test_song_routes import song_dict

D = "window.synthlab.deck"
J = "window.synthlab.jam"
tiled = b2.sized


# --- helpers ----------------------------------------------------------------------


def fast() -> dict:
    """The fixture song (sections s1 Intro / s2 Verse, tracks td / t1 / t2,
    ta audio) at 200 bpm: a bar is 1.2 s, so a quantised launch is soon."""
    return song_dict() | {"bpm": 200.0}


def pad(sid: str, tid: str) -> str:
    return f"#jam-pad-{sid}-{tid}"


def _wait(page, check, what: str, timeout: float = 8.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(40)
        waited += 0.04
    raise AssertionError(f"timed out waiting for {what}")


def _open(studio, server, song: dict | None = None):
    use(server, song or fast())
    page = studio.open("song")
    page.wait_for_selector("#jam-toggle")
    page.wait_for_function(f"() => !!{J}")
    studio.settle()
    return page


def sounding(page) -> dict:
    """{track id: section id} of what the launcher has started."""
    return {x["tid"]: x["sid"] for x in page.evaluate(f"() => {J}.sounding()")}


def wait_sounding(page, want: dict, what: str):
    _wait(page, lambda: sounding(page) == want, f"{what} (have {sounding(page)})")


def wait_started(page):
    """Every loop the launcher has scheduled is past its start. `sounding`
    turns as a launch is SCHEDULED (the scheduler looks 120 ms ahead), so a
    press made straight after it can still land on that same bar line —
    and the state in between then lasts no bars and captures as nothing."""
    page.wait_for_function(
        f"() => {J}.sounding().every("
        "(x) => x.at <= window.synthlab.getCtx().currentTime)"
    )


def on_the_grid(page) -> list[float]:
    """Every sounding loop's start, in bars of the jam's clock."""
    return page.evaluate(
        f"() => {J}.sounding().map((x) => (x.at - {J}.t0()) / {J}.bar())"
    )


def depth(server) -> int:
    return server.state.song_undo_state()["depth"]


def captured(server, was: int) -> list[dict]:
    return server.state.song["sections"][was:]


def _captures_the_bass(page, server, was_sections: int, was_depth: int):
    """What every input's journey ends on: one new section playing t1's
    Riff, one undo step, the launcher put away."""
    try:
        _wait(
            page,
            lambda: len(server.state.song["sections"]) > was_sections,
            "the capture's post",
        )
    except AssertionError as err:
        # what the page knew when no capture arrived
        seen = page.evaluate(
            f"""() => ({{ on: {J}.on(), events: {J}.events(), bars: {J}.bars(),
              disabled: document.querySelector('#jam-capture')
                ?.getAttribute('aria-disabled'),
              toasts: document.querySelector('#toasts')?.innerText.slice(0, 200),
              busy: document.querySelectorAll('.htmx-request').length }})"""
        )
        raise AssertionError(f"{err} — the page: {seen}") from None
    new = captured(server, was_sections)
    assert new[0]["cells"]["t1"] == "p1", new
    assert depth(server) == was_depth + 1
    page.wait_for_function(
        "() => !document.querySelector('#view').classList.contains('jam-on')"
    )
    assert page.evaluate(f"() => !{J}.on() && {J}.events().length === 0")
    assert not sounding(page)


# --- mouse ------------------------------------------------------------------------


def test_jam_by_mouse_launch_switch_scene_stop_capture_undo(studio, server):
    page = _open(studio, server)
    n, d = len(server.state.song["sections"]), depth(server)
    assert page.locator("#arr-scroll").is_visible()
    assert not page.locator("#jam").is_visible()
    page.click("#jam-toggle")
    page.wait_for_selector("#view.jam-on")
    assert page.locator("#jam").is_visible()
    assert not page.locator("#arr-scroll").is_visible()  # the playlist is put away
    assert page.get_attribute("#jam-toggle", "aria-pressed") == "true"
    assert page.get_attribute("#jam-capture", "aria-disabled") == "true"
    # one pad: music, with no setup
    page.click(pad("s1", "t1"))
    wait_sounding(page, {"t1": "s1"}, "the pad")
    studio.wait_sound()
    assert page.get_attribute(pad("s1", "t1"), "aria-pressed") == "true"
    _wait(
        page,
        lambda: page.inner_text("#jam-says").startswith("bar "),
        "the bar clock",
    )
    # its own meter moves, and the loop's place is drawn on the pad
    _wait(
        page,
        lambda: page.evaluate(
            "() => parseFloat(document.querySelector('#jam-meter-t1')"
            ".style.getPropertyValue('--level')) > 0"
        ),
        "the row's meter",
    )
    assert page.evaluate(
        "(s) => document.querySelector(s).style.getPropertyValue('--p') !== ''",
        pad("s1", "t1"),
    )
    # a second pad: queued (it says so, with a countdown), then on a bar line
    # (pressed early in a bar, so the bar line is not already inside the
    # scheduler's 120 ms and the queued state can be seen)
    page.wait_for_function(
        f"() => (((window.synthlab.getCtx().currentTime - {J}.t0())"
        f" / {J}.bar()) % 1) < 0.4"
    )
    page.click(pad("s2", "t2"))
    assert "queued" in page.get_attribute(pad("s2", "t2"), "class")
    page.wait_for_function(
        "() => document.querySelector('#jam-says').textContent"
        ".startsWith('next: Hook in ')"
    )
    wait_sounding(page, {"t1": "s1", "t2": "s2"}, "the second pad")
    for at in on_the_grid(page):
        assert at == pytest.approx(round(at), abs=1e-6) and at >= 0, at
    assert "queued" not in page.get_attribute(pad("s2", "t2"), "class")
    # a scene's head: the whole column, every track on the same sample
    wait_started(page)
    page.click("#jam-scene-s2")
    wait_sounding(page, {"td": "s2", "t1": "s2", "t2": "s2"}, "the scene")
    starts = on_the_grid(page)
    assert len(set(starts)) == 1 and starts[0] == pytest.approx(round(starts[0]))
    assert page.get_attribute("#jam-scene-s2", "aria-pressed") == "true"
    assert page.get_attribute("#jam-scene-s1", "aria-pressed") == "false"
    # a row's stop: that track, on the next bar
    wait_started(page)
    page.click("#jam-off-td")
    wait_sounding(page, {"t1": "s2", "t2": "s2"}, "the row's stop")
    # stop all: now
    page.click("#jam-stop")
    wait_sounding(page, {}, "stop all")
    studio.wait_silence()
    assert page.get_attribute("#jam-capture", "aria-disabled") == "false"
    assert "launch" in page.inner_text("#jam-log")
    # capture: sections on the timeline, one undo step
    page.click("#jam-capture")
    _captures_the_bass(page, server, n, d)
    assert page.locator("#arr-scroll").is_visible()  # the playlist, with them on it
    assert page.locator(".arr-section").count() == len(server.state.song["sections"])
    names = [s["name"] for s in captured(server, n)]
    assert names[0] == "Intro" and "Verse" in names  # named for what was launched
    page.click("#song-undo")
    _wait(page, lambda: len(server.state.song["sections"]) == n, "the undo")


def test_the_song_and_the_jam_never_play_over_each_other(studio, server):
    page = _open(studio, server)
    page.click("#song-play")
    page.wait_for_function("() => window.synthlab.songClock().playing")
    page.click("#jam-toggle")
    page.wait_for_function("() => !window.synthlab.songClock().playing")
    page.click(pad("s1", "td"))
    wait_sounding(page, {"td": "s1"}, "the pad")
    page.click("#song-play")  # (Space on the focused pad would launch it again)
    page.wait_for_function("() => window.synthlab.songClock().playing")
    wait_sounding(page, {}, "the jam giving way to the song")
    page.click("#song-stop")
    # leaving the page stops a jam
    page.click(pad("s1", "td"))
    wait_sounding(page, {"td": "s1"}, "the pad again")
    studio.nav("mixer")
    wait_sounding(page, {}, "the jam stopping with the page")
    studio.settle()


def test_the_mode_and_the_quantise_survive_a_reload_and_an_edit(studio, server):
    page = _open(studio, server)
    page.click("#jam-toggle")
    page.select_option("#jam-q", "4")
    page.reload()
    page.wait_for_selector("#view.jam-on #jam")
    assert page.input_value("#jam-q") == "4"
    assert page.get_attribute("#jam-toggle", "aria-pressed") == "true"
    page.select_option("#jam-q", "1")
    # an edit redraws the launcher under what sounds: the pad stays lit
    page.click(pad("s1", "t1"))
    wait_sounding(page, {"t1": "s1"}, "the pad")
    page.evaluate(
        "() => htmx.ajax('POST', '/song/section/s2/duplicate', { swap: 'none' })"
    )
    _wait(page, lambda: len(server.state.song["sections"]) == 3, "the edit")
    page.wait_for_function("() => document.querySelectorAll('.jam-scene').length === 3")
    assert page.locator("#jam").is_visible()
    assert page.get_attribute(pad("s1", "t1"), "aria-pressed") == "true"
    assert sounding(page) == {"t1": "s1"}
    # ... and the switch, which the topbar's morph had written back to "off"
    page.wait_for_selector('#jam-toggle[aria-pressed="true"]')
    page.click("#jam-stop")
    # "forget" drops the log; with nothing in it Capture is off
    assert page.get_attribute("#jam-forget", "aria-disabled") == "false"
    page.click("#jam-forget")
    assert page.get_attribute("#jam-capture", "aria-disabled") == "true"
    assert page.evaluate(f"() => {J}.events().length") == 0
    studio.settle()


def test_a_refused_capture_keeps_the_log(studio, server):
    page = _open(studio, server)
    page.click("#jam-toggle")
    page.click(pad("s1", "t1"))
    wait_sounding(page, {"t1": "s1"}, "the pad")
    page.wait_for_function(f"() => {J}.bars() >= 1")  # it has started
    page.click("#jam-stop")
    # the section the jam launched is removed under it
    song = server.state.song
    song["sections"] = [s for s in song["sections"] if s["id"] != "s1"]
    use(server, song)
    page.click("#jam-capture")
    toast = page.wait_for_selector("#toasts .toast.error")
    assert "no longer has" in toast.inner_text(), toast.inner_text()
    assert page.evaluate(f"() => {J}.on() && {J}.events().length > 0")
    studio.settle()
    # the 422 is the refusal itself (the browser logs every failed request)
    assert any("/song/jam/commit" in p and "422" in p for p in studio.problems)
    studio.problems[:] = [p for p in studio.problems if "/song/jam/commit" not in p]


# --- keyboard only ----------------------------------------------------------------


def _in_grid(page) -> str:
    return page.evaluate(
        "() => (document.activeElement?.closest('.jam-grid')"
        " ? document.activeElement.id : '')"
    )


def test_jam_by_keyboard_alone(studio, server):
    studio.page.add_init_script(COUNT_POINTERS)
    page = _open(studio, server)
    n, d = len(server.state.song["sections"]), depth(server)
    k = Keys(page)
    k.press("Alt+j")
    page.wait_for_selector("#view.jam-on")
    # the grid is one tab stop; the switch put the keyboard on it
    assert _in_grid(page) == "jam-scene-s1"
    assert (
        page.evaluate(
            "() => [...document.querySelectorAll('.jam-grid button')]"
            ".filter((b) => b.tabIndex === 0).length"
        )
        == 1
    )
    k.press("ArrowDown")
    assert _in_grid(page) == "jam-pad-s1-td"
    k.press("ArrowDown")
    assert _in_grid(page) == "jam-pad-s1-t1"
    k.press("ArrowDown")  # t2 has no block in the Intro: the cursor stays
    assert _in_grid(page) == "jam-pad-s1-t1"
    k.press("Enter")  # launch
    wait_sounding(page, {"t1": "s1"}, "Enter on a pad")
    assert _in_grid(page) == "jam-pad-s1-t1"
    k.press("ArrowRight")
    assert _in_grid(page) == "jam-pad-s2-t1"
    k.press("Space")  # launch, too — and the song did not start
    wait_sounding(page, {"t1": "s2"}, "Space on a pad")
    assert not page.evaluate("() => window.synthlab.songClock().playing")
    k.press("Shift+Enter")  # the pad's whole scene
    wait_sounding(page, {"td": "s2", "t1": "s2", "t2": "s2"}, "Shift+Enter")
    k.press("Backspace")  # stop the row
    wait_sounding(page, {"td": "s2", "t2": "s2"}, "Backspace")
    k.press("End")
    assert _in_grid(page) == "jam-off-t1"
    k.press("Home")
    assert _in_grid(page) == "jam-pad-s1-t1"
    k.press("ArrowUp")
    k.press("ArrowUp")
    assert _in_grid(page) == "jam-scene-s1"
    k.press("Escape")  # a key stops
    wait_sounding(page, {}, "Escape")
    k.press("Alt+1")  # a scene by number
    wait_sounding(page, {"td": "s1", "t1": "s1"}, "Alt+1")
    k.press("Alt+0")
    wait_sounding(page, {}, "Alt+0")
    assert _in_grid(page)  # still inside the surface
    k.press("Alt+c")
    _captures_the_bass(page, server, n, d)
    # the grid is put away: the keyboard is on its switch, not on <body>
    _wait(
        page,
        lambda: page.evaluate("() => document.activeElement?.id") == "jam-toggle",
        "the focus after the capture",
    )
    assert k.pointers() == 0
    studio.settle()


def test_the_palette_has_the_jams_commands(studio, server):
    page = _open(studio, server)
    # (`true`: the ones that are off for now too — Capture, with an empty log)
    names = page.evaluate("() => window.synthlab.commands(true).map((c) => c.label)")
    for name in (
        "Jam: on / off",
        "Launch scene 1",
        "Launch scene 2",
        "Stop all",
        "Capture the jam to the song",
        "Forget the jam",
    ):
        assert name in names, name


# --- pen (synthetic) --------------------------------------------------------------


def test_jam_by_pen(studio, server):
    studio.page.add_init_script(PEN)
    page = _open(studio, server)
    n, d = len(server.state.song["sections"]), depth(server)
    p = Pen(page)
    p.tap("#jam-toggle")
    page.wait_for_selector("#view.jam-on")
    # hover says what a pad is (shell.js lends the title to its tooltip)
    said = page.get_attribute(pad("s1", "t1"), "title") or page.get_attribute(
        pad("s1", "t1"), "aria-label"
    )
    assert "Bass" in said and "Riff" in said and "Intro" in said
    p.hover(pad("s1", "t1"))
    p.tap(pad("s1", "t1"))
    wait_sounding(page, {"t1": "s1"}, "the pen's pad")
    # it has started (a launch is scheduled a moment ahead: a stop inside
    # that moment lands on bar 0 with it, and there is nothing to capture)
    page.wait_for_function(f"() => {J}.bars() >= 1")
    p.tap("#jam-off-t1")
    wait_sounding(page, {}, "the pen's stop")
    p.tap("#jam-capture")
    _captures_the_bass(page, server, n, d)
    studio.settle()


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


def test_jam_by_touch_sizes_and_taps(surface, server):
    page = _open(surface, server)
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    n, d = len(server.state.song["sections"]), depth(server)
    t = Touch(page)

    def mid(sel):
        b = page.locator(sel).bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    t.tap(*mid("#jam-toggle"))
    page.wait_for_selector("#view.jam-on")
    sizes = page.evaluate(
        """() => Object.fromEntries(Object.entries({
             toggle: '#jam-toggle', pad: '#jam-pad-s1-t1', scene: '#jam-scene-s2',
             off: '#jam-off-t1', stop: '#jam-stop', capture: '#jam-capture',
             forget: '#jam-forget', quantise: '#jam-q',
           }).map(([k, s]) => {
             const b = document.querySelector(s).getBoundingClientRect();
             return [k, [Math.round(b.width), Math.round(b.height)]]; }))"""
    )
    for name, (w, h) in sizes.items():
        assert w >= 40 and h >= 40, (name, sizes)
    t.tap(*mid(pad("s1", "t1")))
    wait_sounding(page, {"t1": "s1"}, "the finger's pad")
    assert page.evaluate("() => scrollX === 0 && scrollY === 0")
    page.wait_for_function(f"() => {J}.bars() >= 1")  # it has started
    t.tap(*mid("#jam-off-t1"))
    wait_sounding(page, {}, "the finger's stop")
    # the stop redraws the jam bar: a tap on a button replaced under the
    # finger makes no click (one run in three lost the capture), so let the
    # bar settle and Capture be live before the finger lands on it
    page.wait_for_function(
        "() => document.querySelector('#jam-capture')"
        "?.getAttribute('aria-disabled') === 'false'"
    )
    page.wait_for_timeout(300)
    t.tap(*mid("#jam-capture"))
    _captures_the_bass(page, server, n, d)
    surface.settle()


# --- deck -------------------------------------------------------------------------


def test_jam_on_the_deck_its_key_page(studio, server):
    page = _open(studio, server)
    n, d = len(server.state.song["sections"]), depth(server)
    page.wait_for_function(f"() => !!{D}")

    def key(i):
        page.evaluate(f"(i) => {D}.handle({{ type: 'keydown', index: i }})", i)
        page.wait_for_timeout(80)

    def face():
        return page.evaluate(
            f"() => {D}.face().keys.map((k) => [k.label, k.on, k.off])"
        )

    for _ in range(5):  # "More": the sixth page
        key(7)
    assert [k[0] for k in face()] == [
        "Launcher",
        "Scene 1",
        "Scene 2",
        "Scene 3",
        "Scene 4",
        "Stop all",
        "Capture",
        "More",
    ]
    key(0)
    page.wait_for_selector("#view.jam-on")
    lit = {label: on for label, on, _off in face()}
    assert lit["Launcher"] is True
    off = {label: off for label, _on, off in face()}
    assert not off["Scene 1"] and not off["Scene 2"]
    assert off["Scene 3"] and off["Scene 4"]  # the song has two sections
    key(1)
    wait_sounding(page, {"td": "s1", "t1": "s1"}, "the Scene 1 key")
    _wait(
        page,
        lambda: {label: on for label, on, _off in face()}["Scene 1"] is True,
        "the Scene 1 key lit while it sounds",
    )
    key(5)
    wait_sounding(page, {}, "the Stop all key")
    key(6)
    _captures_the_bass(page, server, n, d)
    studio.settle()


# --- an empty song, rule 1 --------------------------------------------------------


def test_an_empty_song_with_jam_on_still_offers_the_starter_and_it_sounds(
    studio, server
):
    page = studio.open("song")
    page.wait_for_selector("#jam-toggle")
    assert page.is_disabled("#jam-toggle")  # nothing to launch yet
    page.evaluate(
        "() => localStorage.setItem('synthlab.jam', JSON.stringify({ on: true }))"
    )
    page.reload()
    page.wait_for_selector(".arr-start")
    assert page.locator(".arr-start").is_visible()  # no launcher: the starter sheet
    assert page.locator("#jam").count() == 0
    page.locator(".arr-start [data-cmd^='Start a song']").first.click()
    studio.wait_sound(timeout=25)
    # the song it made plays; the launcher is there for it, Jam still on
    page.wait_for_selector("#view.jam-on #jam .jam-pad")
    assert page.locator("#jam").is_visible()
    page.click("#song-stop")
    studio.settle()


@pytest.mark.parametrize("song", ["fixture", "starter"])
def test_rule_1_holds_on_the_launcher(tiled, server, song):
    from fastapi.testclient import TestClient

    open_, bare = tiled
    if song == "starter":
        hx = {"HX-Request": "true", "HX-Current-URL": f"{server.url}/view/song"}
        c = TestClient(server.app, base_url=server.url)
        assert (
            c.post("/song/start", data={"style": "trance"}, headers=hx).status_code
            == 200
        )
    else:
        use(server, fast())
    got = {}
    for w, h in b2.SIZES:
        page = open_("song", w, h)
        page.keyboard.press("Alt+j")
        page.wait_for_selector("#view.jam-on #jam")
        page.wait_for_timeout(200)
        got[w] = round(bare(page), 1)
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        assert over[0] <= 1 and over[1] <= 1, (w, over)  # the view is the window
        box = page.locator("#jam").bounding_box()
        pane = page.locator("#song-arranger").bounding_box()
        assert box["height"] >= pane["height"] - 16, (w, box, pane)  # it fills its pane
        # every pad is a target
        small = page.evaluate(
            "() => [...document.querySelectorAll('.jam-pad, .jam-scene, .jam-off')]"
            ".map((b) => b.getBoundingClientRect())"
            ".filter((r) => r.height < 40 || r.width < 40).length"
        )
        assert small == 0, (w, small)
    print(f"\nrule 1, the launcher ({song}): bare background {got} %")
    assert all(v < 5.0 for v in got.values()), got
