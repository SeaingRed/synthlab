"""Front-end bugs and their fixes, in Chromium.

What these journeys prove: Back and Forward bring a page back inside the
shell — the rail, both docks, the toasts and the AudioContext all survive
(htmx 4's history restore used to swap the whole <body> for the bare
view); an `hx-confirm` asks in the page's own popover, and the song keeps
sounding while it is open (the browser's confirm() stopped every script,
and song.js schedules 1.5 s ahead); "Open Song ▸" swaps the page in place
(it reloaded: the audio, the held keys, the song stopped); a pattern
tool's menu is a popover that Esc and a click away close, in the top layer
(a panel's overflow clipped the <details>), whose rows the arrows walk.
The sound is the master analyser's level — nobody has listened.
`tests/studio/test_frontend_contracts.py` holds the same fixes in the sources.
"""

from __future__ import annotations

# a mark the page keeps only while it is not reloaded, on the window and on
# the AudioContext itself
MARK = """() => { window.__alive = 1; const c = window.synthlab.getCtx();
  if (c) c.__mark = 1; return c?.state ?? null; }"""
SAME = """() => window.__alive === 1 && window.synthlab.getCtx()?.__mark === 1
  && window.synthlab.getCtx().state === "running" """
SHELL = """() => ({
  rail: document.querySelectorAll("nav.views a.view").length,
  led: !!document.getElementById("audio-led"),
  topdock: !!document.querySelector(".topdock"),
  keys: !!document.getElementById("keys-dock"),
  toasts: !!document.getElementById("toasts"),
  views: document.querySelectorAll("#view").length,
  here: document.querySelector('nav.views a[aria-current="page"]')
    ?.getAttribute("href"),
})"""


def _shell_holds(page, view: str) -> None:
    s = page.evaluate(SHELL)
    assert (
        s["rail"] >= 12 and s["led"] and s["topdock"] and s["keys"] and s["toasts"]
    ), s
    assert s["views"] == 1 and s["here"] == f"/view/{view}", s


def test_back_and_forward_keep_the_shell_and_the_audio(studio):
    page = studio.open("editor")
    studio.start_voice()
    assert page.evaluate(MARK) == "running"
    assert page.title() == "Synths · synthlab studio"
    studio.nav("drums")
    studio.settle()
    assert page.title() == "Kit · synthlab studio"
    page.go_back()
    page.wait_for_selector('#view[data-view="editor"]')
    studio.settle()
    _shell_holds(page, "editor")
    assert page.evaluate(SAME), "Back reloaded the page or lost the AudioContext"
    assert page.title() == "Synths · synthlab studio"
    page.go_forward()
    page.wait_for_selector('#view[data-view="drums"]')
    studio.settle()
    _shell_holds(page, "drums")
    assert page.evaluate(SAME)
    assert page.title() == "Kit · synthlab studio"
    studio.start_voice()  # and the keys still play through it


def _a_long_song(studio, server):
    from dataclasses import replace

    from synthlab.studio import beatgen

    a = server.state.arrangement
    drums = beatgen.generate("four-floor", 1, a.drum_bars, a.drum_steps)
    server.state.arrangement = replace(a, drums=drums)
    page = studio.open("song")
    studio.settle()
    # eight sections of the loop (a bar each): long enough to sound through
    # an open question and on — one bar ended before the question did
    for n in range(1, 9):
        page.click('button[hx-post="/song/from-loop"]')  # "Add loop to song"
        page.wait_for_function(
            "(n) => document.querySelectorAll('.arr-section').length >= n", arg=n
        )
        studio.settle()
    assert len(server.state.song["sections"]) == 8
    return page


def test_a_confirm_asks_in_the_page_and_the_song_plays_on(studio, server):
    page = _a_long_song(studio, server)
    posts: list[str] = []
    page.on(
        "request",
        lambda r: (
            r.method == "POST" and "/song/energy" in r.url and posts.append(r.url)
        ),
    )
    page.click("#song-play")
    page.wait_for_function("() => window.synthlab.songClock().playing")
    studio.wait_sound()
    by_energy = '#song-arranger button[hx-post="/song/energy"][hx-confirm]'
    page.click(by_energy)
    page.wait_for_selector("#confirm-pop:popover-open")
    assert "Arrange by energy?" in page.inner_text("#confirm-pop .confirm-q")
    # under its button, OK holding the keyboard (Enter)
    pop, btn = (
        page.locator("#confirm-pop").bounding_box(),
        page.locator(by_energy).bounding_box(),
    )
    assert (
        pop["y"] >= btn["y"] + btn["height"] - 2
        or pop["y"] + pop["height"] <= btn["y"] + 2
    ), (pop, btn)
    assert page.evaluate(
        "() => document.activeElement?.closest('#confirm-pop .btn-primary') !== null"
    )
    # past song.js's 1.5 s of scheduled audio, the page's scripts still run it
    t0 = page.evaluate("() => window.synthlab.getCtx().currentTime")
    for _ in range(3):
        page.wait_for_timeout(900)
        studio.wait_sound(timeout=1.5)
    assert page.evaluate("() => window.synthlab.getCtx().currentTime") - t0 > 2.5
    assert page.evaluate("() => window.synthlab.songClock().playing")
    assert page.is_visible("#confirm-pop") and not posts  # still asking, nothing sent
    # Esc: no request
    page.keyboard.press("Escape")
    page.wait_for_selector("#confirm-pop:not(:popover-open)", state="attached")
    page.wait_for_timeout(300)
    assert not posts
    # a click away: none either
    page.click(by_energy)
    page.wait_for_selector("#confirm-pop:popover-open")
    page.click(".view-head h1")  # (an empty spot: nothing of its own to press)
    page.wait_for_selector("#confirm-pop:not(:popover-open)", state="attached")
    page.wait_for_timeout(300)
    assert not posts
    # OK: the request leaves, once — a question asked from inside a menu
    # (the last section's ⋯ Delete) nests in it, and Enter answers it
    last = server.state.song["sections"][-1]["id"]
    page.focus(
        f'[popovertarget="secmenu-{last}"]'
    )  # (a narrow head: its resize edge is over it)
    page.keyboard.press("Enter")
    page.wait_for_selector(f"#secmenu-{last}:popover-open")
    deletes: list[str] = []
    page.on(
        "request",
        lambda r: (
            r.method == "POST"
            and r.url.endswith(f"/song/section/{last}/delete")
            and deletes.append(r.url)
        ),
    )
    page.click(f"#secmenu-{last} button.danger")
    page.wait_for_selector("#confirm-pop:popover-open")
    assert page.evaluate(
        f"() => document.getElementById('secmenu-{last}').matches(':popover-open')"
    )
    with page.expect_response(f"**/song/section/{last}/delete"):
        page.keyboard.press("Enter")
    studio.settle()
    assert len(deletes) == 1 and len(server.state.song["sections"]) == 7 and not posts
    assert page.evaluate("() => window.synthlab.songClock().playing")
    studio.wait_sound()  # (the song plays on through the redraw)
    page.click("#song-play")


def test_open_song_swaps_the_page_and_keeps_the_audio(studio):
    page = studio.open("perform")
    studio.start_voice()
    assert page.evaluate(MARK) == "running"
    page.click("#perform-arrange .to-song")
    page.wait_for_selector("#perform-arrange .to-song-note .tsn-go")
    go = page.locator("#perform-arrange .tsn-go")
    assert (
        go.get_attribute("hx-get") == "/view/song"
        and go.get_attribute("href") == "/view/song"
    )
    go.click()
    page.wait_for_selector('#view[data-view="song"]')
    studio.settle()
    assert page.evaluate(SAME), "Open Song ▸ reloaded the page"
    assert page.url.endswith("/view/song") and page.title() == "Song · synthlab studio"
    _shell_holds(page, "song")


def test_a_pattern_tools_menu_is_a_popover_esc_and_a_click_away_close(studio):
    page = studio.open("perform")
    studio.settle()
    opener, menu = '#transport [popovertarget="ptm-nvel"]', "#ptm-nvel"
    page.click(opener)
    page.wait_for_selector(f"{menu}:popover-open")
    # in the top layer, whole: what is at its rows' centre is its row
    box = page.locator(menu).bounding_box()
    vw = page.viewport_size
    assert (
        box["x"] >= 0 and box["x"] + box["width"] <= vw["width"] and box["height"] > 40
    ), box
    hit = page.evaluate(
        """(sel) => {
           const row = document.querySelector(sel + " .menu-item");
           const b = row.getBoundingClientRect();
           const at = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
           return !!at?.closest(sel); }""",
        menu,
    )
    assert hit, "the menu is covered or clipped"
    page.keyboard.press("Escape")
    page.wait_for_selector(f"{menu}:not(:popover-open)", state="attached")
    # a click away
    page.click(opener)
    page.wait_for_selector(f"{menu}:popover-open")
    page.click(".view-head h1")
    page.wait_for_selector(f"{menu}:not(:popover-open)", state="attached")
    # from the keyboard: Enter opens, ↓ walks into it, Esc gives the button back
    page.focus(opener)
    page.keyboard.press("Enter")
    page.wait_for_selector(f"{menu}:popover-open")
    page.keyboard.press("ArrowDown")
    assert page.evaluate(f"() => document.activeElement.matches('{menu} .menu-item')")
    first = page.evaluate("() => document.activeElement.textContent.trim()")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.textContent.trim()") != first
    page.keyboard.press("Escape")
    page.wait_for_selector(f"{menu}:not(:popover-open)", state="attached")
    assert page.evaluate(f"() => document.activeElement.matches('{opener}')")
    # a row runs its tool and the menu closes with it
    page.click(opener)
    with page.expect_response("**/fragment/pattern/notes/vel_ramp"):
        page.click(f"{menu} .menu-item:has-text('ramp')")
    page.wait_for_selector(f"{menu}:not(:popover-open)", state="attached")
    studio.settle()


def test_the_reset_menu_stays_open_through_a_redraw_of_its_region(studio):
    page = studio.open("editor")
    studio.settle()
    page.click('[popovertarget="reset-panel"]')
    page.wait_for_selector("#reset-panel:popover-open")
    # an edit elsewhere redraws the region out of band (its dots): morphed,
    # it stays open
    page.evaluate(
        """() => window.htmx.ajax("POST", "/fragment/patch", {
             source: document.getElementById("post-from"),
             values: { cutoff: "900" }, swap: "none" })"""
    )
    studio.settle()
    assert page.evaluate(
        "() => document.getElementById('reset-panel').matches(':popover-open')"
    )
    page.keyboard.press("Escape")
    page.wait_for_selector("#reset-panel:not(:popover-open)", state="attached")
