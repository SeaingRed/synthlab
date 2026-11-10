"""Journey 12: the clip library — one drawer, on every page.

The drawer (`#clip-drawer`, region `clip-library`) is a declarative
popover in base.html, outside `#view`. These drive it as a user does:

- it opens from the rail button on Synths, Perform, Voice and Song, with
  the count, the groups, and that page's own action on each clip;
- typing in the search filters the list and never takes the keyboard out
  of the box; ▶ plays a clip (the master analyser carries signal);
- ✎ renames in place, the drawer still open;
- Voice: "Use as take" fills the take slot; Song: "Place" lands a take in
  the section and bar chosen, and Ctrl+Z takes it back;
- it stays open across a view change made from the keyboard and across a
  re-fetch of `#view` caused by another page's edit; Esc closes it — and
  only it: the Song page's own Esc (its pattern drawer) does not fire too;
- a used clip's delete asks first, saying where it is used;
- on a phone-wide screen it is a bottom sheet inside the viewport.

A pointer click outside an auto popover closes it — the nav included: that
is the browser's light dismiss, asserted here so it is a known behaviour.

Does not prove how anything sounds; a clip's audio is a test tone.
"""

from __future__ import annotations

import json
import urllib.request

import numpy as np
import pytest

from synthlab.studio import schema
from synthlab.studio.clips import list_clips, save_clip
from tests.browser.conftest import confirm_it, name_it
from tests.studio.test_song_routes import song_dict

DRAWER = "#clip-drawer"
OPEN = "() => document.getElementById('clip-drawer').matches(':popover-open')"


def _tone(seconds: float = 0.6, hz: float = 220.0) -> np.ndarray:
    t = np.arange(int(44100 * seconds)) / 44100
    env = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.01)
    return np.column_stack([np.sin(2 * np.pi * hz * t) * env * 0.6] * 2)


@pytest.fixture
def library(studio):
    """A studio whose clip store holds one clip of each kind, and the song
    routes' fixture song (Intro s1, Verse s2; Vox is its audio track)."""
    d = studio.server.state.data_dir
    save_clip(d, "phrase", _tone(), meta={"source": "voice take"})
    save_clip(d, "jam-one", _tone(hz=330), meta={"source": "jam"})
    save_clip(d, "jam-two", _tone(hz=440), meta={"source": "jam"})
    save_clip(
        d, "groove", _tone(), meta={"source": "Deep Bass", "bpm": 120.0, "bars": 1}
    )
    studio.server.state.song = schema.conform(song_dict(), schema.nodes().SONG)
    return studio


def _open(studio) -> None:
    # the drawer's button says "Clips" (not "Drawer")
    assert (
        studio.page.locator("#clip-library .clips-btn .cb-label")
        .inner_text()
        .startswith("Clips")
    )
    studio.page.click("#clip-library .clips-btn")
    studio.page.wait_for_selector(f"{DRAWER}:popover-open")
    studio.settle()  # the click also asks for the drawer again


def _rows(page) -> list[str]:
    return page.locator(f"{DRAWER} .cd-clip").evaluate_all(
        "(els) => els.map((e) => e.dataset.name)"
    )


def _row(name: str) -> str:
    return f'{DRAWER} .cd-clip[data-name="{name}"]'


@pytest.mark.parametrize("view", ["editor", "perform", "voice", "song"])
def test_the_drawer_opens_on_every_page_with_that_pages_action(library, view):
    page = library.open(view)
    assert page.locator("#clip-library .clip-count").inner_text() == "4"
    assert not page.evaluate(OPEN)
    _open(library)
    heads = page.locator(f"{DRAWER} .cd-group h3").all_inner_texts()
    assert [h.split("\n")[0].strip().upper()[:5] for h in heads] == [
        "LOOPS",  # the Loops first, every page's (none yet here)
        "VOICE",
        "JAMS ",
        "BOUNC",
    ]
    assert _rows(page) == ["phrase", "jam-one", "jam-two", "groove"]
    assert page.locator(f"{DRAWER} .cd-take").count() == (4 if view == "voice" else 0)
    assert page.locator(f"{DRAWER} .cd-place-btn").count() == (
        4 if view == "song" else 0
    )
    assert page.locator(f"{DRAWER} #clip-place").count() == (1 if view == "song" else 0)
    # the drawer is on screen, inside the window
    box = page.locator(DRAWER).bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 1400 and box["height"] > 100
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => !({OPEN[6:]})")


def test_search_filters_the_list_and_keeps_the_keyboard_in_the_box(library):
    page = library.open("editor")
    _open(library)
    page.click("#clip-q")
    page.keyboard.type("JAM", delay=30)
    page.wait_for_function(
        "() => document.querySelectorAll('#clip-drawer .cd-clip').length === 2"
    )
    assert _rows(page) == ["jam-one", "jam-two"]
    # the box was never redrawn: it still has the keyboard and what was typed
    assert page.evaluate("() => document.activeElement.id") == "clip-q"
    page.keyboard.type("-t", delay=30)
    page.wait_for_function(
        "() => document.querySelectorAll('#clip-drawer .cd-clip').length === 1"
    )
    assert page.input_value("#clip-q") == "JAM-t" and _rows(page) == ["jam-two"]
    page.keyboard.type("zz", delay=30)
    page.wait_for_selector(f"{DRAWER} .cd-empty")
    assert "JAM-tzz" in page.locator(f"{DRAWER} .cd-empty").inner_text()
    assert page.evaluate(OPEN)
    # a redraw from inside the drawer keeps the filter and what the box says
    page.fill("#clip-q", "jam")
    page.wait_for_function(
        "() => document.querySelectorAll('#clip-drawer .cd-clip').length === 2"
    )
    page.click(f"{_row('jam-two')} .del summary")
    page.click(f"{_row('jam-two')} .del button")
    page.wait_for_function(
        "() => document.querySelectorAll('#clip-drawer .cd-clip').length === 1"
    )
    assert page.evaluate(OPEN) and page.input_value("#clip-q") == "jam"
    assert page.locator("#clip-library .clip-count").inner_text() == "3"


def test_a_clip_auditions_from_the_drawer(library):
    page = library.open("perform")
    _open(library)
    page.click(f"{_row('jam-one')} .sc-play")
    assert library.wait_sound() > 1e-3
    assert page.evaluate(OPEN)  # hearing one does not close the drawer
    library.wait_silence()


def test_a_clip_renames_in_place_with_the_drawer_still_open(library):
    page = library.open("editor")
    _open(library)
    row = _row("jam-one")
    page.click(f"{row} .ren summary")
    page.fill(f"{row} .ren input[name=new]", "big jam")
    page.keyboard.press("Enter")
    page.wait_for_selector(_row("big jam"))
    assert page.evaluate(OPEN)
    names = [c["name"] for c in list_clips(library.server.state.data_dir)]
    assert "big jam" in names and "jam-one" not in names
    # the count on the rail button and the group are redrawn with it
    assert page.locator("#clip-library .clip-count").inner_text() == "4"
    assert _rows(page) == ["phrase", "big jam", "jam-two", "groove"]


def test_use_as_take_fills_the_voice_take(library):
    page = library.open("voice")
    assert (
        page.locator("#voice-panel .voice-take").get_attribute("data-has-take")
        == "false"
    )
    _open(library)
    page.evaluate("() => { document.getElementById('clip-list').scrollTop = 40; }")
    page.click(f"{_row('phrase')} .cd-take")
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    assert (
        "Take ready — 0.6s"
        in page.locator("#voice-panel .voice-take .voice-status").inner_text()
    )
    assert page.evaluate(OPEN)
    # the vocoder has a take to work on: its button is live
    assert page.locator("#voice-render button[type=submit]").is_enabled()
    assert (library.server.state.data_dir / "voice_take.wav").is_file()


def test_place_lands_a_take_and_ctrl_z_takes_it_back(library):
    page = library.open("song")
    state = library.server.state
    _open(library)
    page.select_option(f"{DRAWER} [name=place_section]", "s2")
    page.wait_for_selector(
        f'{_row("groove")} .cd-place-btn[hx-post="/song/section/s2/clip"]'
    )
    assert page.evaluate(OPEN)  # choosing the section redrew the drawer, open
    page.select_option(f"{DRAWER} [name=at_bar]", "1")
    page.click(f"{_row('groove')} .cd-place-btn")
    page.wait_for_selector(
        '#song-arranger .take-chip[data-clip="groove"]', state="attached"
    )
    take = {"track_id": "ta", "clip": "groove", "at_bar": 1, "gain": 1.0}
    assert state.song["sections"][1]["audio"] == [take]
    # still open, still on the section and bar that were chosen
    assert page.evaluate(OPEN)
    assert page.input_value(f"{DRAWER} [name=place_section]") == "s2"
    assert page.input_value(f"{DRAWER} [name=at_bar]") == "1"
    # the clip is used now: deleting it asks first, and says where
    page.click(f"{_row('groove')} .del summary")
    page.click(f"{_row('groove')} .del button")
    said = confirm_it(page, ok=False)
    assert "1 take in the song" in said, said
    assert "groove" in [c["name"] for c in list_clips(state.data_dir)]  # dismissed
    # Esc closes the drawer; Ctrl+Z on the page then takes the take back
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => !({OPEN[6:]})")
    page.keyboard.press("Control+z")
    page.wait_for_function("() => !document.querySelector('#song-arranger .take-chip')")
    assert state.song["sections"][1]["audio"] == []


def test_esc_closes_the_drawer_and_nothing_else(library):
    """The Song page has its own Esc (it closes the pattern drawer): with
    the clip drawer open, Esc is the popover's, once."""
    page = library.open("song")
    state = library.server.state
    page.locator("#cell-s1-t1 .cell-btn").click()
    page.wait_for_selector("#song-drawer:not([hidden])")
    assert state.song_drawer is not None
    _open(library)
    page.keyboard.press("Escape")
    page.wait_for_function(f"() => !({OPEN[6:]})")
    library.settle()
    assert state.song_drawer is not None, "Esc closed the Song drawer as well"
    assert page.locator("#song-drawer:not([hidden])").count() == 1
    # with the popover gone, the next Esc is the Song page's again
    page.keyboard.press("Escape")
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    assert state.song_drawer is None


def test_the_drawer_stays_open_across_a_view_change_and_a_refetch(library):
    page = library.open("editor")
    _open(library)
    assert page.locator(f"{DRAWER} .cd-take").count() == 0
    # a view change from the keyboard: #view is swapped, the drawer is not in it
    page.focus('nav.views a[href="/view/voice"]')
    page.keyboard.press("Enter")
    page.wait_for_selector('#view[data-view="voice"]')
    page.wait_for_selector(f"{DRAWER} .cd-take")  # and it follows the page
    assert page.evaluate(OPEN)
    library.settle()
    # another page (a second tab) renames a clip: this one re-fetches its
    # view, and the open drawer shows the new name without closing
    body = b"name=groove&new=groove+two"
    headers = {
        "HX-Request": "true",
        "HX-Current-URL": f"{library.url}/view/voice",
        "Origin": library.url,
        "X-Studio-Page": "another-tab",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(
        f"{library.url}/fragment/clip/rename", data=body, headers=headers, method="POST"
    )
    with page.expect_response(lambda r: "/view/voice" in r.url, timeout=8000):
        assert urllib.request.urlopen(req).status == 200
        page.mouse.move(300, 300)  # no pointer is down, no field has focus
    page.wait_for_selector(_row("groove two"))
    assert page.evaluate(OPEN)
    # a pointer click outside is the browser's light dismiss: the nav too
    library.nav("perform")
    assert not page.evaluate(OPEN)
    assert page.locator(f"{DRAWER} .cd-take").count() == 0


def test_bounce_and_keep_land_in_the_library(library):
    """What filled the shelf fills the drawer: ⏺ Bounce on Synths (its
    prompt names the clip), "keep take" on Voice."""
    page = library.open("editor")
    page.click("text=⏺ Bounce")
    name_it(page, "my bounce")
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '5'"
    )
    _open(library)
    assert "my bounce" in _rows(page)
    meta = json.loads(
        (library.server.state.data_dir / "clips" / "my bounce.meta.json").read_text(
            "utf-8"
        )
    )
    assert meta["bars"] and meta["bpm"]
    page.keyboard.press("Escape")
    library.nav("voice")
    _open(library)
    page.click(f"{_row('phrase')} .cd-take")
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    page.keyboard.press("Escape")
    page.click("text=keep take")
    name_it(page, "phrase")  # a taken name
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '6'"
    )
    assert "phrase 2" in [c["name"] for c in list_clips(library.server.state.data_dir)]
    assert "kept as “phrase 2”" in page.locator("#toasts").inner_text()


def test_on_a_phone_the_drawer_is_a_sheet_inside_the_screen(library):
    page = library.page
    page.set_viewport_size({"width": 390, "height": 800})
    library.open("song")
    _open(library)
    box = page.locator(DRAWER).bounding_box()
    assert box["x"] == 0 and round(box["width"]) == 390
    assert box["y"] > 100 and round(box["y"] + box["height"]) == 800  # a bottom sheet
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    # every row's controls are inside it, none cut off to the right
    over = page.locator(f"{DRAWER} .cd-acts > *").evaluate_all(
        "(els) => els.filter((e) =>"
        " e.getBoundingClientRect().right > innerWidth + 1).length"
    )
    assert over == 0
    page.click(f"{DRAWER} .cd-close")
    page.wait_for_function(f"() => !({OPEN[6:]})")
