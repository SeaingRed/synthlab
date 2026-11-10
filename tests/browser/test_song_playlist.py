"""Journey 9: the Song page as a playlist — a bar ruler over
time-true columns, zoom, seek, the section's edge, drags onto a lane, a
block's own edges.

Proves, in a real browser: the measured pixel widths are bars x repeats x
the zoom; the playhead sweeps a repeated section once; each gesture reaches
the song model as one undo step; a trimmed block changes what reaches the
master bus. Does not prove any of it feels right under a hand: Playwright's
mouse is not a person's, and it cannot start a native touch drag at all
(the touch paths are driven with synthetic events, which test the island's
handling, not the browser's).
"""

from __future__ import annotations

import pytest

from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.synth import Patch
from tests.browser.conftest import _INIT, Studio, wav_bytes

SHORT = Patch(name="short", release=0.05).to_json()


def playlist_song(bpm: float = 120.0) -> dict:
    """Intro 2 bars, Verse 4 bars x2, Build 1 bar x3, Outro 8 bars: 21
    bars over drums, two synths and an audio track."""
    return {
        "bpm": bpm,
        "root": "A1",
        "scale_name": "minor",
        "tracks": [
            {"id": "td", "kind": "drums", "name": "Drums"},
            {"id": "t1", "kind": "synth", "name": "Bass", "patch": SHORT},
            {"id": "t2", "kind": "synth", "name": "Lead", "patch": SHORT},
            {"id": "ta", "kind": "audio", "name": "Vox"},
        ],
        "patterns": {
            "pd": {
                "id": "pd",
                "kind": "drums",
                "name": "Beat",
                "bars": 1,
                "steps": 16,
                "drums": [["kick", 0], ["snare", 8], ["kick", 12]],
            },
            "p1": {
                "id": "p1",
                "kind": "notes",
                "name": "Riff",
                "bars": 1,
                "steps_per_bar": 16,
                "cells": [["A1", 0], ["E2", 4], ["A2", 8], ["E2", 12]],
            },
            "p2": {
                "id": "p2",
                "kind": "notes",
                "name": "Hook",
                "bars": 2,
                "steps_per_bar": 16,
                "cells": [["C3", 2], ["E3", 18]],
            },
        },
        "sections": [
            {"id": "s1", "name": "Intro", "bars": 2, "cells": {"td": "pd"}},
            {
                "id": "s2",
                "name": "Verse",
                "bars": 4,
                "repeats": 2,
                "cells": {"td": "pd", "t1": "p1", "t2": "p2"},
            },
            {
                "id": "s3",
                "name": "Build",
                "bars": 1,
                "repeats": 3,
                "cells": {"td": "pd", "t1": "p1"},
            },
            {"id": "s4", "name": "Outro", "bars": 8, "cells": {"t1": "p1"}},
        ],
    }


def use(server, song: dict | None = None) -> dict:
    server.state.song = schema.conform(song or playlist_song(), schema.nodes().SONG)
    server.state.fx_master = []
    return server.state.song


@pytest.fixture
def touch(chromium, server):
    """1024x768 with a touch screen (a coarse pointer): the Surface's tier."""
    context = chromium.new_context(
        viewport={"width": 1024, "height": 768}, has_touch=True
    )
    context.add_init_script(_INIT)
    context.set_default_timeout(10_000)
    st = Studio(context.new_page(), server)
    yield st
    problems = st.unexplained()
    context.close()
    assert not problems, "the page reported errors:\n  " + "\n  ".join(problems)


def _wait(page, check, what: str) -> None:
    for _ in range(160):
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(what)


GEO = """() => {
  const view = document.querySelector(".song-view");
  const barW = parseFloat(getComputedStyle(view).getPropertyValue("--bar-w"));
  const ruler = document.getElementById("song-ruler").getBoundingClientRect();
  return {
    barW,
    ruler: [ruler.left, ruler.width],
    sections: [...document.querySelectorAll(".arr-section")].map((th) => {
      const r = th.getBoundingClientRect();
      const d = th.dataset;
      return { id: d.sectionId, bars: +d.bars, reps: +d.repeats,
               start: +d.startBar, left: r.left, width: r.width };
    }),
    cells: [...document.querySelectorAll(".arr-cell")].map((td) => {
      const r = td.getBoundingClientRect();
      return { id: td.id, sid: td.dataset.sectionId, left: r.left, width: r.width };
    }),
    blocks: [...document.querySelectorAll(".cell-btn.blk")].map((b) => {
      const r = b.getBoundingClientRect();
      return { cell: b.closest("td").id, left: r.left, width: r.width };
    }),
    echoes: [...document.querySelectorAll(".echo:not(.take-echo)")].map((e) => {
      const r = e.getBoundingClientRect();
      return { cell: e.closest("td").id, left: r.left, width: r.width };
    }),
    numbers: [...document.querySelectorAll(".ruler-n")].map((n) =>
      [+n.textContent, n.getBoundingClientRect().left, getComputedStyle(n).display]),
    scrolls: document.documentElement.scrollWidth <= innerWidth,
  };
}"""


def _check_geometry(page) -> dict:
    """Every x on the playlist is bars x --bar-w, to half a pixel."""
    g = page.evaluate(GEO)
    w = g["barW"]
    assert w > 0
    at = None
    for sec in g["sections"]:
        want = sec["bars"] * sec["reps"] * w
        assert abs(sec["width"] - want) <= 0.5, (sec, want)
        if at is not None:  # no gap between columns
            assert abs(sec["left"] - at) <= 0.5, (sec, at)
        at = sec["left"] + sec["width"]
        # the ruler's bar 1 is the first section's left edge
        x = g["ruler"][0] + (sec["start"] - 1) * w
        assert abs(sec["left"] - x) <= 0.5, (sec, x)
    total = sum(s["bars"] * s["reps"] for s in g["sections"])
    assert abs(g["ruler"][1] - total * w) <= 0.5
    secs = {s["id"]: s for s in g["sections"]}
    for cell in g["cells"]:
        sec = secs[cell["sid"]]
        assert abs(cell["width"] - sec["width"]) <= 0.5, cell
        assert abs(cell["left"] - sec["left"]) <= 0.5, cell
    cells = {c["id"]: c for c in g["cells"]}
    for blk in g["blocks"]:  # a block: the section's first pass
        cell = cells[blk["cell"]]
        sec = secs[cell["sid"]]
        assert abs(blk["left"] - cell["left"]) <= 0.5, blk
        assert abs(blk["width"] - (sec["bars"] * w - 1)) <= 0.5, blk
    for n, left, display in g["numbers"]:  # number N sits over bar N
        if display != "none":
            assert abs(left - (g["ruler"][0] + (n - 1) * w + 4)) <= 0.5, (n, left)
    assert g["scrolls"], "the page itself scrolls sideways"
    return g


# --- 1. geometry ------------------------------------------------------------------


def test_every_section_is_as_wide_as_its_bars_times_its_repeats(studio, server):
    use(server)
    page = studio.open("song")
    studio.settle()
    g = _check_geometry(page)
    by = {s["id"]: s for s in g["sections"]}
    # the Verse (4 bars x2) is four times the Intro (2 bars), the Build
    # (1 bar x3) a bar and a half of it: the picture is time
    assert by["s2"]["width"] == pytest.approx(4 * by["s1"]["width"], abs=0.5)
    assert by["s3"]["width"] == pytest.approx(1.5 * by["s1"]["width"], abs=0.5)
    assert [n for n, _x, _d in g["numbers"]] == [1, 5, 9, 13, 17, 21]
    # one echo a later pass, each a pass further along
    echoes = [e for e in g["echoes"] if e["cell"] == "cell-s3-td"]
    blk = next(b for b in g["blocks"] if b["cell"] == "cell-s3-td")
    assert len(echoes) == 2
    assert [e["left"] - blk["left"] for e in echoes] == pytest.approx(
        [g["barW"], 2 * g["barW"]], abs=0.5
    )
    # an echo is not a target: a click on it falls through to the cell
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.echo')).pointerEvents"
        )
        == "none"
    )


def test_the_playlist_keeps_its_time_axis_on_a_touch_screen(touch, server):
    use(server)
    page = touch.open("song")
    touch.settle()
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    _check_geometry(page)
    # the section's edge is a finger-sized target
    grip = page.locator("#sec-s2 .sec-resize").bounding_box()
    assert grip["width"] >= 28


def test_a_phone_scrolls_the_same_timeline_beside_a_narrow_track_head(chromium, server):
    use(server)
    context = chromium.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
    )
    context.add_init_script(_INIT)
    st = Studio(context.new_page(), server)
    page = st.open("song")
    st.settle()
    _check_geometry(page)
    head = page.locator(".track-head").first.bounding_box()
    assert head["width"] == pytest.approx(96, abs=1)
    # the level left the head for the track's menu
    assert not page.locator(".track-mix .track-gain").first.is_visible()
    page.locator(".track-top .sec-menu-btn").first.click()
    assert page.locator(".menu-gain .track-gain").first.is_visible()
    problems = st.unexplained()
    context.close()
    assert not problems, problems


# --- 2. the playhead --------------------------------------------------------------


def _playhead(page) -> float | None:
    return page.evaluate(
        """() => {
          const ph = document.querySelector(".song-playhead");
          if (!ph || ph.hidden) return null;
          return parseFloat(ph.style.translate);
        }"""
    )


def test_the_playhead_crosses_an_echo_without_jumping_back(studio, server):
    """The Build is one bar played three times. The playhead used to sweep
    its column once a pass; on the playlist the section is three bars wide
    and the playhead crosses it once."""
    use(server, playlist_song(bpm=200.0))  # a bar is 1.2 s
    page = studio.open("song")
    studio.settle()
    # select the Build by its head (a bar wide, its first pass is all menu
    # button: the click lands in its second pass)
    page.locator("#sec-s3 .sec-block").click(position={"x": 40, "y": 10})
    page.wait_for_selector("#sec-s3.selected")
    page.click('[data-song-action="play"]')
    _wait(page, lambda: _playhead(page) is not None, "the playhead never showed")
    studio.wait_sound()
    g = page.evaluate(GEO)
    build = next(s for s in g["sections"] if s["id"] == "s3")
    xs = []
    for _ in range(60):  # ~3 s: into the third pass
        x = _playhead(page)
        if x is None or "Build" not in page.inner_text("#song-position"):
            break
        xs.append(x)
        page.wait_for_timeout(50)
    page.click('[data-song-action="stop"]')
    assert len(xs) > 20
    assert all(b >= a - 0.5 for a, b in zip(xs, xs[1:], strict=False)), xs
    # it went past the first pass: more than a bar of travel inside the Build
    assert xs[-1] - xs[0] > 1.2 * g["barW"], (xs[0], xs[-1])
    assert xs[-1] - xs[0] <= build["width"] + 1
    studio.settle()


# --- 3. zoom and fit ----------------------------------------------------------------

ZOOM = "document.querySelector('.song-view').dataset.barW"


def _bar_w(page) -> float:
    return page.evaluate(GEO)["barW"]


STEPS = (8, 12, 16, 24, 32, 48, 64, 96)


def _zoom(page, how: str, times: int) -> None:
    """Press a zoom button `times` times with the mouse where it is. Not
    `page.click`: a pressed .btn rises back to its hover lift (style.css, a
    `translate` transition), so a quick next click
    found it "not stable", and Playwright's retry scrolls its target into
    view by other alignments (end, center, start) — which, for a button in
    the sticky corner, scrolled `#arr-scroll` (384 px at 96 px a bar)."""
    box = page.locator(f'[data-zoom-to="{how}"]').bounding_box()
    for _ in range(times):
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _zoom_is(page, px: float) -> None:
    """`data-bar-w` is the step at or under the width (a fit is between steps)."""
    step = max(s for s in STEPS if s <= px)
    page.wait_for_function(f"() => {ZOOM} === '{step}'")
    assert abs(_bar_w(page) - px) < 0.01


def test_zoom_in_out_and_fit_keep_the_geometry_and_survive_a_swap_and_a_reload(
    studio, server
):
    use(server)
    page = studio.open("song")
    studio.settle()
    first = _bar_w(page)
    # a first visit: "fit" — the lanes' width, between the steps — never
    # under 16 (and no cap at 48)
    assert 16 <= first <= 96
    _zoom_is(page, first)

    page.click('[data-zoom-to="1"]')
    page.click('[data-zoom-to="1"]')
    up = _bar_w(page)
    assert up > first
    _check_geometry(page)
    page.click('[data-zoom-to="-1"]')
    assert first < _bar_w(page) < up
    _check_geometry(page)
    for _ in range(9):  # the bottom step holds
        page.click('[data-zoom-to="-1"]')
    assert _bar_w(page) == 8
    _check_geometry(page)
    # zoomed out, every other number is hidden
    shown = [n for n, _x, d in page.evaluate(GEO)["numbers"] if d != "none"]
    assert shown == [1, 9, 17]

    page.click('[data-zoom-to="fit"]')
    fit = _bar_w(page)
    g = _check_geometry(page)
    # 21 bars in the lanes: exactly, to a quarter px (it was the largest
    # step that showed them all, which left the lanes' end empty)
    lanes = page.evaluate(
        "() => { const sc = document.getElementById('arr-scroll');"
        " return sc.clientWidth - sc.querySelector('.arr-corner').offsetWidth; }"
    )
    assert fit == min(96, max(8, int(lanes / 21 * 4) / 4))
    assert fit * 21 <= lanes
    assert g["barW"] == fit

    # a region swap (the arranger is replaced) keeps the zoom ...
    page.click('[data-zoom-to="-1"]')
    kept = _bar_w(page)
    page.evaluate(
        "() => htmx.ajax('POST', '/song/section/s1/repeats',"
        " { values: { repeats: '2' }, swap: 'none' })"
    )
    _wait(
        page,
        lambda: server.state.song["sections"][0]["repeats"] == 2,
        "the repeat edit never landed",
    )
    page.wait_for_selector('#sec-s1[data-repeats="2"]')
    assert _bar_w(page) == kept
    _check_geometry(page)
    # ... and so does a reload (it is this browser's)
    page.reload()
    page.wait_for_selector('#view[data-view="song"]')
    _zoom_is(page, kept)
    _check_geometry(page)
    # and a view swap away and back (the nav replaces #view)
    studio.nav("drums")
    studio.nav("song")
    _zoom_is(page, kept)
    _check_geometry(page)
    studio.settle()


def test_ctrl_wheel_outside_the_lanes_is_not_a_zoom_of_the_song_view(studio, server):
    """`.song-view` held its bar width as `data-zoom`, so zoom.js took the
    whole view for a scroller named "24" and wrote `--zoom-24` on <html>
    for a Ctrl+wheel over the top bar. The attribute is `data-bar-w` now:
    only `#arr-scroll` is a zoom scroller on this page."""
    use(server, playlist_song())
    page = studio.open("song")
    studio.settle()
    assert page.evaluate(
        "() => [...document.querySelectorAll('[data-zoom]')].map((e) => e.id)"
    ) == ["arr-scroll"]
    was = _bar_w(page)
    box = page.locator("#song-topbar").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -100)
    page.keyboard.up("Control")
    page.wait_for_timeout(150)
    assert _bar_w(page) == was
    assert "--zoom-" not in page.evaluate(
        "() => document.documentElement.style.cssText"
    )


def test_ctrl_wheel_zooms_about_the_pointer(studio, server):
    """The bar under the pointer stays under it."""
    song = playlist_song()
    song["sections"][3]["repeats"] = 8  # 77 bars: the lanes scroll
    use(server, song)
    page = studio.open("song")
    studio.settle()

    def under(x: float) -> float:
        return page.evaluate(
            "(x) => { const r = document.getElementById('song-ruler')"
            ".getBoundingClientRect(); const v = document.querySelector('.song-view');"
            " return (x - r.left) / parseFloat(getComputedStyle(v)"
            ".getPropertyValue('--bar-w')); }",
            x,
        )

    page.evaluate("() => { document.getElementById('arr-scroll').scrollLeft = 200; }")
    box = page.locator("#arr-scroll").bounding_box()
    x, y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.5
    page.mouse.move(x, y)
    before, was = under(x), _bar_w(page)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -100)
    page.keyboard.up("Control")
    page.wait_for_function(f"() => {ZOOM} !== '{int(was)}'")
    assert _bar_w(page) > was
    assert under(x) == pytest.approx(before, abs=0.1)
    _check_geometry(page)
    # a plain wheel scrolls, it does not zoom
    now = _bar_w(page)
    page.mouse.wheel(0, 100)
    page.wait_for_timeout(150)
    assert _bar_w(page) == now
    studio.settle()


# --- 4. seek --------------------------------------------------------------------------


def _mark(page) -> list:
    return page.evaluate(
        """() => {
          const r = document.getElementById("song-ruler");
          const m = r.querySelector(".ruler-mark");
          return [m && !m.hidden ? Number(m.style.getPropertyValue("--b")) : null,
                  Number(r.getAttribute("aria-valuenow")),
                  window.synthlab.songClock().sel, window.synthlab.songFrom()];
        }"""
    )


def _click_bar(page, bar: int) -> None:
    """Click the ruler in the middle of 1-based timeline bar `bar`."""
    g = page.evaluate(GEO)
    box = page.locator("#song-ruler").bounding_box()
    page.mouse.click(g["ruler"][0] + (bar - 0.5) * g["barW"], box["y"] + 8)


def _position(page) -> str:
    return page.inner_text("#song-position")


def test_the_ruler_sets_a_start_marker_and_play_starts_from_it(studio, server):
    use(server, playlist_song(bpm=200.0))  # a bar is 1.2 s
    page = studio.open("song")
    studio.settle()
    assert _mark(page)[0] is None
    assert page.evaluate("() => window.synthlab.songFrom()") == 0

    _click_bar(page, 8)  # bar 8 is bar 6 of the Verse (bars 3-10)
    assert _mark(page) == [7, 8, "s2", 5]
    page.wait_for_selector("#sec-s2.selected")
    assert not page.evaluate("() => window.synthlab.songClock().playing")  # no play

    page.keyboard.press("Space")  # the ruler has focus: Space is Play
    _wait(page, lambda: _position(page).startswith("bar 8 /"), "never at bar 8")
    studio.wait_sound()
    assert page.evaluate("() => window.synthlab.songClock().playing")

    # a click while it plays jumps there
    _click_bar(page, 2)
    assert _mark(page) == [1, 2, "s1", 1]
    _wait(page, lambda: _position(page).startswith("bar 2 /"), "never at bar 2")
    page.click('[data-song-action="stop"]')

    # the keyboard: the ruler is a tab stop
    page.focus("#song-ruler")
    page.keyboard.press("ArrowRight")
    assert _mark(page) == [2, 3, "s2", 0]  # bar 3: the Verse's first
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    assert _mark(page) == [0, 1, "s1", 0]
    page.keyboard.press("ArrowLeft")  # held at bar 1
    assert _mark(page)[0] == 0
    for _ in range(4):
        page.keyboard.press("ArrowRight")
    assert _mark(page) == [4, 5, "s2", 2]
    page.keyboard.press("Enter")  # plays from the marker
    _wait(page, lambda: _position(page).startswith("bar 5 /"), "never at bar 5")
    page.click('[data-song-action="stop"]')
    page.focus("#song-ruler")
    page.keyboard.press("Home")
    assert _mark(page) == [0, 1, "s1", 0]

    # selecting another section moves the marker to its first bar
    page.locator("#cell-s4-t1 .cell-btn").click()
    page.wait_for_selector("#sec-s4.selected")
    _wait(page, lambda: _mark(page)[:3] == [13, 14, "s4"], "the marker stayed")
    # the marker is redrawn after a swap replaces the arranger
    page.keyboard.press("Escape")
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    studio.settle()
    assert _mark(page)[0] == 13
    studio.settle()


def test_the_scroller_follows_the_playhead_off_the_right_edge(studio, server):
    song = playlist_song(bpm=200.0)
    song["sections"][3]["repeats"] = 4  # 45 bars
    use(server, song)
    page = studio.open("song")
    studio.settle()
    _zoom(page, "1", 8)  # 96 px a bar: about 9 bars show
    assert _bar_w(page) == 96
    scroll = "() => document.getElementById('arr-scroll').scrollLeft"
    assert page.evaluate(scroll) == 0
    box = page.locator("#arr-scroll").bounding_box()
    head = page.locator(".arr-corner").bounding_box()["width"]
    last = int((box["width"] - head) // 96)  # the last bar wholly on screen
    _click_bar(page, last)
    page.locator("#song-play").focus()  # (the pointer has left the scroller)
    page.keyboard.press("Space")
    _wait(page, lambda: page.evaluate(scroll) > 0, "the scroller never followed")
    # it paged on: the playhead is on screen again
    page.wait_for_timeout(300)
    x = page.evaluate(
        "() => document.querySelector('.song-playhead').getBoundingClientRect().left"
    )
    assert box["x"] + head <= x <= box["x"] + box["width"]
    page.click('[data-song-action="stop"]')
    studio.settle()


# --- 5. a section's edge --------------------------------------------------------------


def _says(page, title: str) -> None:
    """The topbar's undo button reads `title` (the edit's redraw landed)."""
    _wait(
        page,
        lambda: page.get_attribute("#song-undo", "title") == title,
        f"undo never said {title!r}: {page.get_attribute('#song-undo', 'title')!r}",
    )


def _drag_edge(page, selector: str, bars: float, *, alt=False, hold=None) -> None:
    """Drag a handle `bars` bars to the right (negative: left) with a real
    mouse; `hold` runs while the button is still down."""
    w = _bar_w(page)
    box = page.locator(selector).bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    if alt:
        page.keyboard.down("Alt")
    page.mouse.down()
    page.mouse.move(x + bars * w / 2, y, steps=4)
    page.mouse.move(x + bars * w, y, steps=4)
    if hold:
        hold()
    page.mouse.up()
    if alt:
        page.keyboard.up("Alt")


def _refused(st, path: str) -> None:
    """A refusal the journey asked for: the route answered 422 with its
    toast, and Chromium logs every failed fetch as a console error. That
    one line is expected; anything else still fails the test."""
    mine = [p for p in st.problems if "422" in p and path in p]
    assert mine, f"{path} was not refused: {st.problems}"
    st.problems[:] = [p for p in st.problems if p not in mine]


def _section(server, sid: str) -> dict:
    return next(s for s in server.state.song["sections"] if s["id"] == sid)


def test_a_sections_edge_drags_its_repeats_and_alt_its_bars(
    studio, server, monkeypatch
):
    # the drag below is sized for a 60-bar cap: 240 s at 60 bpm, which was the
    # song cap until it became 480 s (a drag past 120 bars leaves the window)
    from synthlab.studio import limits

    monkeypatch.setattr(limits, "MAX_SONG_SECONDS", 240.0)
    use(server, playlist_song(bpm=60.0))  # the cap is 60 bars
    page = studio.open("song")
    studio.settle()
    _zoom(page, "-1", 8)  # 8 px a bar: long drags stay on screen
    assert _bar_w(page) == 8

    seen = {}

    def during():
        # only CSS has moved: the head is a pass wider, the tag says x3,
        # the model has not been asked yet
        seen["tag"] = page.get_attribute("#sec-s2 .sec-resize", "data-tag")
        seen["width"] = page.locator("#sec-s2").bounding_box()["width"]
        seen["reps"] = _section(server, "s2")["repeats"]
        seen["selected"] = page.evaluate("() => String(getSelection())")

    # the Verse is 4 bars x2: one pass (4 bars) to the right is x3
    _drag_edge(page, "#sec-s2 .sec-resize", 4, hold=during)
    assert seen == {"tag": "×3", "width": 4 * 3 * 8, "reps": 2, "selected": ""}
    _wait(page, lambda: _section(server, "s2")["repeats"] == 3, "repeats did not move")
    page.wait_for_selector('#sec-s2[data-repeats="3"]')
    _says(page, "Undo: section repeats (Ctrl+Z)")
    _check_geometry(page)
    assert page.evaluate("() => String(getSelection())") == ""  # no text selected

    # one undo step
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(page, lambda: _section(server, "s2")["repeats"] == 2, "undo did nothing")
    page.wait_for_selector('#sec-s2[data-repeats="2"]')

    # back two passes: held at x1
    _drag_edge(page, "#sec-s2 .sec-resize", -12)
    _wait(page, lambda: _section(server, "s2")["repeats"] == 1, "repeats not held at 1")
    page.wait_for_selector('#sec-s2[data-repeats="1"]')

    # Alt: the same edge sets the section's bars
    _drag_edge(page, "#sec-s2 .sec-resize", 2, alt=True, hold=during)
    assert seen["tag"] == "6 bars"
    _wait(page, lambda: _section(server, "s2")["bars"] == 6, "bars did not move")
    page.wait_for_selector('#sec-s2[data-bars="6"]')
    _says(page, "Undo: section length (Ctrl+Z)")
    _check_geometry(page)

    # a drag that moves nothing posts nothing
    before = page.get_attribute("#song-undo", "title")
    _drag_edge(page, "#sec-s2 .sec-resize", 0.3)
    page.wait_for_timeout(200)
    assert page.get_attribute("#song-undo", "title") == before

    # past the 240 s cap: the route's toast, and the edge snaps back
    assert _section(server, "s4") == {**_section(server, "s4"), "bars": 8, "repeats": 1}
    _drag_edge(page, "#sec-s4 .sec-resize", 8 * 6)  # x7: 23 + 56 bars > 60
    page.wait_for_selector('.toast:has-text("the cap is 240s")')
    _refused(studio, "/song/section/s4/repeats")
    assert _section(server, "s4")["repeats"] == 1
    _wait(
        page,
        lambda: page.locator("#sec-s4").bounding_box()["width"] == 8 * 8,
        "the edge did not snap back",
    )
    _check_geometry(page)
    studio.settle()


def test_esc_cancels_an_edge_drag(studio, server):
    use(server)
    page = studio.open("song")
    studio.settle()
    before = page.locator("#sec-s2").bounding_box()["width"]
    _drag_edge(
        page, "#sec-s2 .sec-resize", 4, hold=lambda: page.keyboard.press("Escape")
    )
    page.wait_for_timeout(200)
    assert _section(server, "s2")["repeats"] == 2
    assert page.locator("#sec-s2").bounding_box()["width"] == before
    assert page.get_attribute("#sec-s2 .sec-resize", "data-tag") is None
    studio.settle()


def test_a_touch_drag_on_the_sections_edge_sets_its_repeats(touch, server):
    """Pointer events with pointerType touch (dispatched: Playwright has no
    touch drag). The handle is `touch-action: none`, so the browser would
    hand the gesture to the page rather than scroll."""
    use(server)
    page = touch.open("song")
    touch.settle()
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.sec-resize')).touchAction"
        )
        == "none"
    )
    w = _bar_w(page)
    page.evaluate(
        """([dx]) => {
          const h = document.querySelector("#sec-s2 .sec-resize");
          const r = h.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + 5;
          const fire = (type, cx) => h.dispatchEvent(new PointerEvent(type, {
            bubbles: true, cancelable: true, pointerId: 7, pointerType: "touch",
            clientX: cx, clientY: y, button: 0 }));
          h.setPointerCapture = () => {};  // (a synthetic pointer cannot be captured)
          fire("pointerdown", x); fire("pointermove", x + dx / 2);
          fire("pointermove", x + dx); fire("pointerup", x + dx);
        }""",
        [4 * w],
    )
    _wait(page, lambda: _section(server, "s2")["repeats"] == 3, "the touch drag failed")
    page.wait_for_selector('#sec-s2[data-repeats="3"]')
    touch.settle()


# --- 6. drags onto a lane -------------------------------------------------------------


def _cells(server, sid: str) -> dict:
    return dict(_section(server, sid)["cells"])


def _takes(server, sid: str) -> list[tuple]:
    return [
        (a["clip"], a["at_bar"], a["track_id"]) for a in _section(server, sid)["audio"]
    ]


def _bar_x(page, cell: str, bar: float) -> tuple[float, float]:
    """The page point in the middle of 0-based bar `bar` of a lane cell."""
    box = page.locator(cell).bounding_box()
    return box["x"] + (bar + 0.5) * _bar_w(page), box["y"] + box["height"] / 2


def _drag(page, src: tuple, dst: tuple, *, key: str | None = None, during=None):
    """A native drag with the real mouse, from one page point to another
    (Chromium turns the move into dragstart / dragover / drop). Raw mouse,
    not `drag_to`: a drop under the clip drawer is not "actionable" until
    the drag itself has made the drawer see-through."""
    page.mouse.move(*src)
    page.mouse.down()
    if key:
        page.keyboard.down(key)
    page.mouse.move(src[0] + 6, src[1] + 4, steps=3)
    page.mouse.move(dst[0] - 4, dst[1], steps=6)
    page.mouse.move(*dst, steps=2)
    if during:
        during()
    page.mouse.up()
    if key:
        page.keyboard.up(key)


def _center(page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_a_pool_pattern_dropped_on_a_cell_fills_its_section(studio, server):
    use(server)
    page = studio.open("song")
    studio.settle()
    assert _cells(server, "s4").get("t2") is None
    seen = {}

    def during():
        # the preview: the whole section, and the cell says it takes it
        m = page.locator("#cell-s4-t2 .drop-at").bounding_box()
        seen["len"] = round(m["width"] / _bar_w(page))
        seen["ok"] = page.locator("#cell-s4-t2.drop-ok").count()

    pool = _center(page, "#pool-p2 .pool-open-pattern")
    _drag(page, pool, _bar_x(page, "#cell-s4-t2", 5), during=during)
    _wait(
        page, lambda: _cells(server, "s4").get("t2") == "p2", "the drop filled nothing"
    )
    assert seen == {"len": 8, "ok": 1}
    page.wait_for_selector("#cell-s4-t2.filled")
    _says(page, "Undo: set cell (Ctrl+Z)")
    assert page.locator(".drop-at").count() == 0  # the preview is gone
    # a block fills its section: no span came from where it was dropped
    assert "spans" not in _section(server, "s4")
    # the other kind of track: the cell says no, and the drop is a toast
    _drag(
        page,
        _center(page, "#pool-p2 .pool-open-pattern"),
        _bar_x(page, "#cell-s4-td", 2),
    )
    page.wait_for_selector(".toast:has-text('go on this track')")
    assert _cells(server, "s4").get("td") is None
    studio.settle()


def test_a_block_dragged_to_another_cell_moves_and_ctrl_copies(studio, server):
    """The plain drag MOVES now (it copied for a week); Ctrl copies. Each is
    one undo step."""
    use(server)
    page = studio.open("song")
    studio.settle()
    seen = {}

    def during():
        seen["copy"] = page.locator("#cell-s4-td.drop-copy").count()
        seen["ok"] = page.locator("#cell-s4-td.drop-ok").count()

    # the Build's beat onto the Outro's empty drums cell: it moves
    _drag(
        page,
        _bar_x(page, "#cell-s3-td", 0),
        _bar_x(page, "#cell-s4-td", 3),
        during=during,
    )
    _wait(
        page, lambda: _cells(server, "s4").get("td") == "pd", "the drop moved nothing"
    )
    assert seen == {"copy": 0, "ok": 1}
    assert _cells(server, "s3").get("td") is None  # the source is cleared
    page.wait_for_selector("#cell-s3-td:not(.filled)")
    _says(page, "Undo: move cell (Ctrl+Z)")

    # one Ctrl+Z puts it back where it was, and gone from the Outro
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(
        page,
        lambda: (
            _cells(server, "s3").get("td") == "pd"
            and not _cells(server, "s4").get("td")
        ),
        "one Ctrl+Z did not take the move back",
    )
    page.wait_for_selector("#cell-s3-td.filled")

    # Ctrl-drag: a copy by reference; the source still plays it
    _drag(
        page, _bar_x(page, "#cell-s3-td", 0), _bar_x(page, "#cell-s4-td", 3),
        key="Control", during=during,
    )  # fmt: skip
    _wait(
        page,
        lambda: _cells(server, "s4").get("td") == "pd",
        "the Ctrl-drop copied nothing",
    )
    assert seen == {"copy": 1, "ok": 1}
    assert _cells(server, "s3").get("td") == "pd"
    _says(page, "Undo: set cell (Ctrl+Z)")
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(page, lambda: not _cells(server, "s4").get("td"), "Ctrl+Z kept the copy")
    studio.settle()

    # a click is still a click: it opens the drawer, nothing moves
    page.locator("#cell-s3-td .cell-btn").click()
    page.wait_for_selector('#song-drawer:not([hidden])[data-kind="drums"]')
    assert _cells(server, "s3").get("td") == "pd"
    studio.settle()


def _clip(server, name: str, seconds: float) -> None:
    from synthlab.studio.clips import clip_dir

    (clip_dir(server.data_dir) / f"{name}.wav").write_bytes(wav_bytes(seconds))


def _open_library(page) -> dict:
    page.click(".clips-btn")
    page.wait_for_selector("#clip-drawer:popover-open .cd-clip")
    page.wait_for_load_state("networkidle")
    return page.locator("#clip-drawer").bounding_box()


def test_a_clip_dragged_out_of_the_library_drawer_lands_at_the_bar_under_the_pointer(
    studio, server
):
    """The drawer is a top-layer popover over the right of the page. A row
    dragged out of it: the drawer turns see-through to the pointer, so a
    lane UNDER it takes the drop; it is not closed (a drag starts with the
    pointer inside it, so the browser's light dismiss never sees an outside
    click), and it is still open afterwards."""
    use(server, playlist_song(bpm=120.0))
    _clip(server, "hit", 2.0)  # one bar at 120 bpm
    for i in range(26):  # a full drawer: it reaches down over the (now taller) lanes
        _clip(server, f"other {i:02d}", 0.2)
    page = studio.open("song")
    studio.settle()
    # the playlist takes the window's width, so its first
    # "fit" is a wider bar: zoom out until the first target is clear of the
    # drawer (before the drawer opens — a click outside would close it)
    page.click('[data-zoom-to="-1"]')
    drawer = _open_library(page)
    row = page.locator('.cd-clip[data-name="hit"]')
    assert row.get_attribute("draggable") == "true"
    assert row.get_attribute("data-bars") == "1.0"

    # 1. onto the Verse's audio lane, bar 3 of its first pass
    seen = {}

    def during():
        m = page.locator("#cell-s2-ta .drop-at").bounding_box()
        cell = page.locator("#cell-s2-ta").bounding_box()
        seen["at"] = round((m["x"] - cell["x"]) / _bar_w(page))
        seen["len"] = round(m["width"] / _bar_w(page))
        seen["out"] = page.locator("#clip-library.dragging-out").count()

    target = _bar_x(page, "#cell-s2-ta", 2)
    assert target[0] < drawer["x"], "the first target should be clear of the drawer"
    _drag(
        page, _center(page, '.cd-clip[data-name="hit"] .sc-name'), target, during=during
    )
    _wait(page, lambda: _takes(server, "s2") == [("hit", 2, "ta")], "no take landed")
    assert seen == {"at": 2, "len": 1, "out": 1}
    page.wait_for_selector('#cell-s2-ta .take-chip[data-clip="hit"]')
    _says(page, "Undo: place clip (Ctrl+Z)")
    assert page.locator("#clip-drawer:popover-open").count() == 1  # still open
    assert page.locator("#clip-library.dragging-out").count() == 0

    # 2. onto an echo: that bar of the pass (bar 2 of the Verse's second pass)
    _drag(
        page,
        _center(page, '.cd-clip[data-name="hit"] .sc-name'),
        _bar_x(page, "#cell-s2-ta", 5),
    )
    _wait(
        page, lambda: ("hit", 1, "ta") in _takes(server, "s2"), "the echo drop missed"
    )
    assert page.locator("#clip-drawer:popover-open").count() == 1

    # 3. onto a lane UNDER the drawer: only possible because it lets go.
    # (A click outside the drawer closes it, as for any popover: the zoom
    # buttons are outside.)
    _zoom(page, "1", 3)
    page.wait_for_selector("#clip-drawer", state="hidden")
    page.evaluate(
        "() => { const sc = document.getElementById('arr-scroll');"
        " sc.scrollLeft = sc.scrollWidth; }"
    )
    drawer = _open_library(page)
    target = _bar_x(page, "#cell-s4-ta", 5)
    assert drawer["x"] < target[0] < drawer["x"] + drawer["width"], (drawer, target)
    under = page.evaluate(
        "([x, y]) => !!document.elementFromPoint(x, y).closest('#clip-drawer')", target
    )
    assert under, "the target is not under the drawer"
    _drag(page, _center(page, '.cd-clip[data-name="hit"] .sc-name'), target)
    _wait(page, lambda: _takes(server, "s4") == [("hit", 5, "ta")], "no drop under it")
    assert page.locator("#clip-drawer:popover-open").count() == 1
    studio.settle()


def test_a_clip_dropped_with_no_audio_track_makes_one(studio, server):
    song = playlist_song()
    song["tracks"] = [t for t in song["tracks"] if t["kind"] != "audio"]
    use(server, song)
    _clip(server, "hit", 2.0)
    page = studio.open("song")
    studio.settle()
    _open_library(page)
    assert not page.locator(".arr-new-audio").is_visible()  # only while dragging
    seen = {}

    def during():
        seen["strip"] = page.locator(".arr-new-audio").is_visible()

    strip = page.locator('.arr-new-audio td[data-section-id="s2"]')
    src = _center(page, '.cd-clip[data-name="hit"] .sc-name')
    page.mouse.move(*src)
    page.mouse.down()
    page.mouse.move(src[0] - 40, src[1] + 10, steps=4)  # the drag has begun
    page.wait_for_selector(".arr-new-audio", state="visible")
    box = strip.bounding_box()
    page.mouse.move(
        box["x"] + 1.5 * _bar_w(page), box["y"] + box["height"] / 2, steps=6
    )
    during()
    page.mouse.up()
    _wait(page, lambda: len(_section(server, "s2")["audio"]) == 1, "nothing landed")
    assert seen == {"strip": True}
    track = server.state.song["tracks"][-1]
    assert track["kind"] == "audio"
    assert _takes(server, "s2") == [("hit", 1, track["id"])]
    page.wait_for_selector(".take-chip")
    assert page.locator(".arr-new-audio").count() == 0  # there is a lane now
    studio.settle()


def _song_with_take(server, at_bar: int = 0) -> None:
    song = playlist_song()
    song["sections"][1]["audio"] = [{"clip": "hit", "at_bar": at_bar, "track_id": "ta"}]
    use(server, song)
    _clip(server, "hit", 4.0)  # two bars at 120 bpm


def test_a_placed_take_is_dragged_to_another_bar_copied_and_removed(studio, server):
    _song_with_take(server)
    page = studio.open("song")
    studio.settle()
    page.click('[data-zoom-to="fit"]')  # the whole song on screen
    chip = '#cell-s2-ta .take-chip[data-clip="hit"]'
    box = page.locator(chip).bounding_box()
    assert box["width"] == pytest.approx(2 * _bar_w(page) - 1, abs=0.5)  # its length

    # grabbed by its second bar and dropped on bar 4: it starts at bar 3
    _drag(page, _bar_x(page, chip, 1), _bar_x(page, "#cell-s2-ta", 3))
    _wait(page, lambda: _takes(server, "s2") == [("hit", 2, "ta")], "the take stayed")
    _says(page, "Undo: move take (Ctrl+Z)")
    page.wait_for_selector(f'{chip}[data-at-bar="2"]')

    # to another section
    _drag(page, _bar_x(page, chip, 0), _bar_x(page, "#cell-s4-ta", 6))
    _wait(page, lambda: _takes(server, "s4") == [("hit", 6, "ta")], "it did not cross")
    assert _takes(server, "s2") == []
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")  # one step
    _wait(page, lambda: _takes(server, "s2") == [("hit", 2, "ta")], "undo did nothing")
    assert _takes(server, "s4") == []
    page.wait_for_selector(f'{chip}[data-at-bar="2"]')

    # Ctrl-drag: a copy (the place route), the take stays
    _drag(page, _bar_x(page, chip, 0), _bar_x(page, "#cell-s1-ta", 1), key="Control")
    _wait(page, lambda: _takes(server, "s1") == [("hit", 1, "ta")], "no copy landed")
    assert _takes(server, "s2") == [("hit", 2, "ta")]
    _says(page, "Undo: place clip (Ctrl+Z)")
    page.wait_for_selector("#cell-s1-ta .take-chip")

    # the keyboard: Delete on the focused take removes it, and the keyboard
    # goes back to the grid rather than to <body>
    page.locator("#cell-s1-ta .take-name").focus()
    page.keyboard.press("Delete")
    _wait(page, lambda: _takes(server, "s1") == [], "Delete removed nothing")
    _says(page, "Undo: remove take (Ctrl+Z)")
    page.wait_for_function("() => document.activeElement?.matches('.arranger-grid')")

    # the menu: its bar field moves the take, Remove takes it off
    page.locator(f"{chip} .take-name").click()
    field = page.locator('.take-menu:popover-open input[name="to_bar"]')
    assert field.input_value() == "3"
    field.fill("1")
    field.press("Tab")  # change: the form posts
    _wait(
        page,
        lambda: _takes(server, "s2") == [("hit", 0, "ta")],
        "the field did nothing",
    )
    page.wait_for_selector(f'{chip}[data-at-bar="0"]')
    page.locator(f"{chip} .take-name").click()
    page.locator(".take-menu:popover-open .take-remove").click()
    _wait(page, lambda: _takes(server, "s2") == [], "Remove removed nothing")
    page.wait_for_function("() => !document.querySelector('.take-chip')")
    studio.settle()


_SYNTH_DRAG = """([from, to]) => {
  const src = document.querySelector(from), dst = document.querySelector(to);
  const dt = new DataTransfer(), r = dst.getBoundingClientRect();
  const fire = (el, type) => el.dispatchEvent(new DragEvent(type, {
    bubbles: true, cancelable: true, dataTransfer: dt,
    clientX: r.left + 4, clientY: r.top + 4 }));
  src.dispatchEvent(
    new PointerEvent("pointerdown", { bubbles: true, pointerType: "touch" }));
  fire(src, "dragstart"); fire(dst, "dragover");
  const marked = dst.classList.contains("drop-ok");
  fire(dst, "drop"); fire(src, "dragend");
  return marked;
}"""


def test_a_touch_drop_of_a_take_asks_move_or_copy(touch, server):
    """No modifier under a finger: the drop asks, Move first. Synthetic
    events (Playwright cannot start a native touch drag): the island's
    handling, not the browser's."""
    _song_with_take(server)
    page = touch.open("song")
    touch.settle()
    ids = ['#cell-s2-ta .take-chip[data-clip="hit"]', "#cell-s4-ta"]
    assert page.evaluate(_SYNTH_DRAG, ids) is True
    page.wait_for_selector(".cell-drop:popover-open")
    assert page.locator(".cell-drop button").all_inner_texts() == [
        "Move here",
        "Copy here",
    ]
    assert _takes(server, "s4") == []  # nothing yet: it asked
    page.click('.cell-drop button:has-text("Copy here")')
    _wait(page, lambda: _takes(server, "s4") == [("hit", 0, "ta")], "no copy landed")
    assert _takes(server, "s2") == [("hit", 0, "ta")]
    page.wait_for_selector("#cell-s4-ta .take-chip")
    page.evaluate(
        _SYNTH_DRAG, ['#cell-s2-ta .take-chip[data-clip="hit"]', "#cell-s1-ta"]
    )
    page.click('.cell-drop button:has-text("Move here")')
    _wait(page, lambda: _takes(server, "s1") == [("hit", 0, "ta")], "no move landed")
    assert _takes(server, "s2") == []
    touch.settle()


# --- 7. a whole stem on the timeline (project 4's long item) --------------------------


def _long(server) -> dict:
    (item,) = sm.long_items(server.state.song)
    return item


def _stem_song(server) -> str:
    """A 12 s source with stems in the Lab's store, and the song: 4 bars,
    then 4 bars twice, at 200 bpm (a bar is 1.2 s: the stem is 10 bars),
    its vocal stem on the audio track from bar 1."""
    from tests.studio.test_song_long import audio_song, make_source, place

    sid = make_source(server.data_dir)
    song = audio_song((4, 1), (4, 2))
    place(song, server.data_dir, sid, name="Vox stem")
    use(server, song)
    return sid


def test_a_whole_stem_is_dragged_along_the_timeline_and_trimmed_by_its_edges(
    studio, server
):
    _stem_song(server)
    page = studio.open("song")
    studio.settle()
    assert (_long(server)["at_bar"], _long(server)["start"]) == (0, 0.0)
    parts = page.locator(".long-part")
    assert parts.count() == 2  # one region, drawn through both sections
    a, b = parts.nth(0).bounding_box(), parts.nth(1).bounding_box()
    assert a["x"] + a["width"] == pytest.approx(b["x"], abs=1.5)  # they join
    assert a["width"] + b["width"] == pytest.approx(10 * _bar_w(page), abs=2)

    # grabbed by its second bar, dropped on bar 4: it starts at bar 3
    _drag(page, _bar_x(page, "#cell-s1-ta", 1), _bar_x(page, "#cell-s1-ta", 3))
    _wait(page, lambda: _long(server)["at_bar"] == 2, "the stem did not move")
    _says(page, "Undo: edit stem (Ctrl+Z)")
    item = _long(server)
    # the post carried nothing but its own fields: the name is the stem's
    # (htmx.ajax from <body> would have sent the page's first `name` field)
    assert (item["name"], item["start"], item["length"]) == ("Vox stem", 0.0, 0.0)
    page.wait_for_selector('.long-part[data-at="2"]')

    # grabbed in the NEXT section's strip (its 4th bar on), back to bar 1
    _drag(page, _bar_x(page, "#cell-s2-ta", 1), _bar_x(page, "#cell-s1-ta", 3))
    _wait(page, lambda: _long(server)["at_bar"] == 0, "the far strip did not drag it")
    page.wait_for_selector('.long-part[data-at="0"]')

    # its head: a bar to the right cuts a bar off its start, and what still
    # plays stays where it was (the bar and the source second move together)
    _drag_edge(page, ".long-edge-l", 1)
    _wait(page, lambda: _long(server)["at_bar"] == 1, "the head did not trim")
    assert _long(server)["start"] == pytest.approx(1.2)
    page.wait_for_selector('.long-part[data-at="1"]')
    # its tail: three bars to the left, six bars play
    played = int(page.locator(".long-part").first.get_attribute("data-bars"))
    assert played == 9
    _drag_edge(page, ".long-edge-r", -3)
    _wait(page, lambda: _long(server)["length"] > 0, "the tail did not move")
    assert _long(server)["length"] == pytest.approx(6 * 1.2)
    page.wait_for_selector('.long-part[data-bars="6"]')
    assert _long(server)["name"] == "Vox stem"
    studio.settle()


def test_a_stem_dragged_from_the_library_lands_at_the_bar_under_the_pointer(
    studio, server
):
    sid = _stem_song(server)
    server.state.song = {**server.state.song, "long": []}
    use(server, server.state.song)
    page = studio.open("song")
    studio.settle()
    page.click(".clips-btn")
    page.wait_for_selector("#clip-drawer:popover-open .cd-stem")
    page.wait_for_load_state("networkidle")
    page.uncheck("#long-tempo")  # keep the song's tempo: the bars stay put
    stem = f'.cd-stem[data-source="{sid}"][data-stem="bass"] .cd-dl'
    # bar 2 of the second section's SECOND pass: the timeline's bar 10
    _drag(page, _center(page, stem), _bar_x(page, "#cell-s2-ta", 5))
    _wait(page, lambda: len(sm.long_items(server.state.song)) == 1, "no stem landed")
    item = _long(server)
    assert (item["at_bar"], item["stems"], item["track_id"]) == (9, ["bass"], "ta")
    assert server.state.song["bpm"] == 200.0  # the box was unticked
    page.wait_for_selector(".long-part")
    assert page.locator("#clip-drawer:popover-open").count() == 1
    studio.settle()


# --- 8. a block's own edges: its span ------------------------------------------------


def _span(server, sid: str, tid: str) -> tuple:
    return sm.cell_span(_section(server, sid), tid)


def test_a_blocks_edges_trim_it_and_the_drawer_says_where_it_plays(studio, server):
    use(server)
    page = studio.open("song")
    studio.settle()
    page.click('[data-zoom-to="fit"]')
    cell = "#cell-s4-t1"  # the Outro: 8 bars of the 1-bar Riff
    assert _span(server, "s4", "t1") == (0, 8)
    seen = {}

    def during():
        seen["tag"] = page.get_attribute(f"{cell} .blk-edge-r", "data-tag")
        seen["width"] = round(page.locator(f"{cell} .blk").bounding_box()["width"] + 1)
        seen["model"] = _span(server, "s4", "t1")

    # its right edge, a bar to the left: bars 1-7
    _drag_edge(page, f"{cell} .blk-edge-r", -1, hold=during)
    # (a fit's bar is a quarter px, not a whole one: the width to a px)
    width = seen.pop("width")
    assert seen == {"tag": "bars 1–7", "model": (0, 8)}
    assert width == pytest.approx(7 * _bar_w(page), abs=1)
    _wait(
        page, lambda: _span(server, "s4", "t1") == (0, 7), "the right edge did nothing"
    )
    page.wait_for_selector(f'{cell}.trimmed[data-len="7"]')
    _says(page, "Undo: trim block (Ctrl+Z)")
    # its left edge, two bars to the right: bars 3-7, the pattern starting there
    _drag_edge(page, f"{cell} .blk-edge-l", 2)
    _wait(
        page, lambda: _span(server, "s4", "t1") == (2, 5), "the left edge did nothing"
    )
    page.wait_for_selector(f'{cell}[data-at="2"][data-len="5"]')
    g = page.evaluate(GEO)
    blk = next(b for b in g["blocks"] if b["cell"] == "cell-s4-t1")
    td = next(c for c in g["cells"] if c["id"] == "cell-s4-t1")
    assert blk["left"] - td["left"] == pytest.approx(2 * g["barW"], abs=0.5)
    assert blk["width"] == pytest.approx(5 * g["barW"] - 1, abs=0.5)

    # the drawer reads it back, and its fields are the same edit
    page.locator(f"{cell} .blk").click()
    page.wait_for_selector("#song-drawer .drawer-span")
    studio.settle()
    assert "plays bars 3–7 of Outro" in page.inner_text("#song-drawer .drawer-plays")
    assert page.get_attribute("#song-drawer", "data-at") == "2"
    to = page.locator('#song-drawer .drawer-span input[name="to"]')
    assert to.input_value() == "7"
    to.fill("5")
    to.press("Tab")
    _wait(page, lambda: _span(server, "s4", "t1") == (2, 3), "the field did nothing")
    page.wait_for_function(
        "() => document.querySelector('#song-drawer .drawer-plays')"
        ".textContent.includes('bars 3–5')"
    )
    # Fill section: the block plays through it again
    page.click("#song-drawer .drawer-fill")
    _wait(page, lambda: _span(server, "s4", "t1") == (0, 8), "Fill did nothing")
    page.wait_for_selector(f"{cell}:not(.trimmed)")
    page.wait_for_function(
        "() => document.querySelector('#song-drawer .drawer-plays')"
        ".textContent.includes('plays all of Outro')"
    )
    assert page.get_attribute("#song-drawer", "data-at") is None
    assert "spans" not in _section(server, "s4")
    studio.settle()

    # a double-click on an edge fills the section too
    page.keyboard.press("Escape")
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    _drag_edge(page, f"{cell} .blk-edge-r", -3)
    _wait(page, lambda: _span(server, "s4", "t1") == (0, 5), "no trim before the fill")
    page.wait_for_selector(f'{cell}[data-len="5"]')
    page.locator(f"{cell} .blk-edge-r").dblclick()
    _wait(
        page,
        lambda: _span(server, "s4", "t1") == (0, 8),
        "the double-click did nothing",
    )
    # the edge drag is not a drag of the block, nor a click on it
    assert _cells(server, "s4") == {"t1": "p1"}
    assert page.locator("#song-drawer[hidden]").count() == 1
    studio.settle()


def _spill_song() -> dict:
    """Five 2-bar sections: A plays the Riff, B and C nothing, D the Hook
    (another pattern), E nothing."""
    song = playlist_song()
    song["sections"] = [
        {"id": s, "name": s.upper(), "bars": 2, "cells": cells}
        for s, cells in (
            ("a", {"t1": "p1"}),
            ("b", {}),
            ("c", {}),
            ("d", {"t1": "p2"}),
            ("e", {}),
        )
    ]
    return song


def test_a_right_edge_dragged_past_its_section_spills_until_another_pattern(
    studio, server
):
    use(server, _spill_song())
    page = studio.open("song")
    studio.settle()
    seen = {}

    def during():
        seen["tag"] = page.get_attribute("#cell-a-t1 .blk-edge-r", "data-tag")

    # A's right edge, seven bars on: into E's second bar
    _drag_edge(page, "#cell-a-t1 .blk-edge-r", 7.6, hold=during)
    assert seen["tag"] == "→ E bar 2"
    _wait(page, lambda: _cells(server, "c").get("t1") == "p1", "nothing spilled")
    assert [_cells(server, s).get("t1") for s in "abcde"] == [
        "p1",
        "p1",
        "p1",
        "p2",
        None,
    ]
    page.wait_for_selector(".toast:has-text('now runs on through B and C')")
    _says(page, "Undo: extend block (Ctrl+Z)")
    page.wait_for_selector("#cell-c-t1.filled")
    # one Ctrl+Z takes the whole spill back
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(page, lambda: _cells(server, "b").get("t1") is None, "undo left the spill")
    assert _cells(server, "c").get("t1") is None
    studio.settle()


def test_alt_arrows_slide_the_cursors_block_and_alt_shift_arrows_move_its_end(
    studio, server
):
    use(server)
    page = studio.open("song")
    studio.settle()
    url = page.url
    page.locator("#cell-s4-t1").click(position={"x": 30, "y": 8})
    page.keyboard.press("Escape")  # (the click opened its drawer)
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    studio.settle()
    page.locator(".arranger-grid").focus()
    assert page.evaluate("() => document.querySelector('.arr-cell.cursor')?.id") == (
        "cell-s4-t1"
    )

    def press(keys: str, want: tuple) -> None:
        page.keyboard.press(keys)
        _wait(page, lambda: _span(server, "s4", "t1") == want, f"{keys}: not {want}")
        page.wait_for_selector(
            f'#cell-s4-t1[data-at="{want[0]}"][data-len="{want[1]}"].cursor'
        )
        # the keyboard stays on the grid through every redraw
        page.wait_for_function(
            "() => document.activeElement?.matches('.arranger-grid')"
        )

    press("Alt+Shift+ArrowLeft", (0, 7))  # its end, a bar in
    press("Alt+Shift+ArrowLeft", (0, 6))
    press("Alt+ArrowRight", (1, 6))  # slide: the same length, a bar on
    press("Alt+ArrowRight", (2, 6))
    page.keyboard.press("Alt+ArrowRight")  # no room: it stays, nothing posts
    page.wait_for_timeout(200)
    assert _span(server, "s4", "t1") == (2, 6)
    press("Alt+ArrowLeft", (1, 6))
    press("Alt+Shift+ArrowRight", (1, 7))
    assert page.url == url  # Alt+← was not the browser's Back
    # plain arrows still move the cursor
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("() => document.querySelector('.arr-cell.cursor')?.id") == (
        "cell-s3-t1"
    )
    studio.settle()


def _held_song() -> dict:
    """One 2-bar section at 80 bpm (a bar is 3 s): a lead holding a note
    through every step of its 1-bar pattern, so the section never rests."""
    song = playlist_song(bpm=80.0)
    song["patterns"]["p1"]["cells"] = [["A2", s] for s in range(16)]
    song["sections"] = [{"id": "s", "name": "S", "bars": 2, "cells": {"t1": "p1"}}]
    return song


def test_a_trimmed_block_is_heard_only_in_its_bars(studio, server):
    """The audible result, on the master bus (the analyser the scope draws
    from): untrimmed, the section sounds from its first bar; with the block
    trimmed to its second bar the first bar is silent and the second
    sounds. Proves samples reach the output where the block is and none
    where it is not; nobody hears that they are the right ones
    (tests/studio/test_song_spans.py holds the audio exact)."""
    use(server, _held_song())
    page = studio.open("song")
    studio.settle()

    def play() -> None:
        page.click('[data-song-action="play"]')
        _wait(page, lambda: _playhead(page) is not None, "the playhead never showed")

    play()
    studio.wait_sound(timeout=2.0)  # bar 1 sounds
    page.click('[data-song-action="stop"]')
    studio.wait_silence()

    _drag_edge(page, "#cell-s-t1 .blk-edge-l", 1)  # the block starts at bar 2
    _wait(page, lambda: _span(server, "s", "t1") == (1, 1), "the trim did not land")
    page.wait_for_selector("#cell-s-t1.trimmed")
    studio.settle()

    play()
    quiet = []
    for _ in range(40):  # two seconds of the 3 s first bar
        quiet.append(studio.rms())
        page.wait_for_timeout(50)
    assert max(quiet) < 2e-4, f"bar 1 is not silent: {max(quiet):.2e}"
    assert _position(page).startswith("bar 1 /")
    studio.wait_sound(timeout=4.0)  # ... and bar 2 sounds
    # (the readout is in heard time, an output latency behind the analyser)
    for _ in range(12):
        if _position(page).startswith("bar 2 /"):
            break
        page.wait_for_timeout(50)
    assert _position(page).startswith("bar 2 /"), _position(page)
    page.click('[data-song-action="stop"]')
    studio.settle()


def test_the_drawers_playhead_hides_outside_a_trimmed_block(studio, server):
    song = _held_song()
    song["sections"][0]["spans"] = {"t1": [1, 1]}
    use(server, song)
    page = studio.open("song")
    studio.settle()
    page.locator("#cell-s-t1 .blk").click()  # opens the drawer: the section loops
    page.wait_for_selector("#song-drawer .grid .playhead", state="attached")
    studio.settle()
    page.click('[data-song-action="loop"]') if not page.evaluate(
        "() => window.synthlab.songClock().playing"
    ) else None
    _wait(page, lambda: _playhead(page) is not None, "the loop never started")
    read = """() => {
      const g = document.querySelector("#song-drawer .grid .playhead");
      return [g.hidden, Number(g.style.getPropertyValue("--phase"))];
    }"""
    _wait(page, lambda: _position(page).startswith("bar 1 /"), "not in bar 1")
    assert page.evaluate(read)[0] is True  # bar 1: the block is silent
    _wait(page, lambda: _position(page).startswith("bar 2 /"), "never reached bar 2")
    page.wait_for_timeout(600)
    hidden, phase = page.evaluate(read)
    assert hidden is False and 0.05 < phase < 0.9  # the pattern from its bar 1
    page.click('[data-song-action="stop"]')
    studio.settle()
