"""Perform's Loops panel in Chromium.

What these journeys prove: the panel is folded to a tab at 1400 px and
slides over the page from it (the tab, Alt+O), folds again on a press
elsewhere, and pins as a column; the search reads a loop's sound, key, tempo
and #tags, the kind and ★ filters and a tag chip narrow the list, the sort
moves the rows (bpm, name) — and all of it survives the list being sent
again; ★ and tags are kept on the loop; ▶ on a loop with no audio asks for
it (a render at the tempo playing) and the master carries it; a row dragged
onto the note grid opens it there as the loop, and an edit saves back to
it; ✚ Keep on the drum rack keeps the grid's beat, which the panel then
lists as a beat, and Open puts a beat on the drum grid; a 16-bar loop
opens whole on the roll, which keeps its step width and scrolls, and a
note clicked in bar 16 saves to it. Levels are the
page's analyser's — nobody has listened: whether the preview lands on the
bar line by ear, and the sound, are unchecked.
"""

from __future__ import annotations

import json
import time

from synthlab.studio import loops
from synthlab.studio.state.model import Arrangement
from tests.browser.conftest import name_it
from tests.browser.inputs import clear_of_dock

ROWS = "#loops-panel .lp-row"


def _wait(page, check, what: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _seed(server) -> None:
    """Three loops of notes — two sounds, two keys, three tempos — named by
    what they are, no audio yet."""
    s = server.state
    d = server.data_dir
    for sound, root, scale, bpm, cells in (
        ("Acid bass", "A2", "minor", 124, [["A2", 0], ["C3", 4], ["E3", 8]]),
        ("Glass lead", "F#3", "pentatonic_minor", 96, [["F#3", 0], ["A3", 2]]),
        ("Acid bass", "A2", "minor", 132, [["A2", 0], ["A2", 6]]),
    ):
        arr = Arrangement(bpm=bpm, root=root, scale_name=scale, bars=1, cells=cells)
        payload = s.sound_payload()
        payload["patch"] = {**payload["patch"], "name": sound}
        lane = {"cells": cells, "cuts": []}
        key = {"bpm": bpm, "root": root, "scale_name": scale}
        loops.save(d, loops.auto_name(d, sound, lane, key, 1), arr, payload)
        time.sleep(0.01)


def _shown(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#loops-panel .lp-row')]
             .filter((r) => r.offsetParent !== null).map((r) => r.dataset.title)"""
    )


def _row(page, text: str, bpm: int | None = None):
    at = f'[data-bpm="{bpm}"]' if bpm else ""
    return page.locator(f'{ROWS}[data-title*="{text.lower()}"]{at}').first


def test_the_panel_finds_stars_tags_sorts_and_previews_in_time(studio, server):
    _seed(server)
    page = studio.open("perform")
    studio.settle()
    root = "document.documentElement.dataset.loops"
    assert page.evaluate(f"() => {root}") == "folded"  # 1400 px: a tab
    assert not page.locator(ROWS).first.is_visible()
    page.locator(".view-head h1").click()
    page.keyboard.press("Alt+O")  # opens, sliding over, the search focused
    assert page.evaluate(f"() => {root}") == "over"
    assert page.evaluate("() => document.activeElement.id") == "lp-q"
    beats = page.locator(f'{ROWS}[data-what="beat"]').count()
    assert beats >= 7  # the Drums bank's slots, as beats
    # search: a sound, a key, a tempo — every word must match
    page.keyboard.type("acid")
    assert len(_shown(page)) == 2 and all("acid" in t for t in _shown(page))
    page.fill("#lp-q", "pent 96")
    # a name says what the loop is; its key, tempo and bars
    # are its cells — the search reads them all the same
    assert _shown(page) == ["glass lead riff"]
    glass_cells = _row(page, "glass lead").locator(".lp-facts .fc")
    assert glass_cells.all_inner_texts()[:4] == ["1 bar", "1/4", "96", "Am pent"]
    page.fill("#lp-q", "")
    # the kind
    page.click('[data-lp-kind="beat"]')
    assert len(_shown(page)) == beats
    page.click('[data-lp-kind="notes"]')
    assert len(_shown(page)) == 3
    # ★, kept on the loop, and its filter
    acid = _row(page, "acid bass riff", bpm=124)
    with page.expect_response("**/loop/star"):
        acid.locator(".lp-star").click()
    name = acid.get_attribute("data-ctx").removeprefix("loop ")
    _wait(page, lambda: loops.load(server.data_dir, name)["star"], "the ★ kept")
    page.wait_for_selector(
        f'{ROWS}[data-star][data-ctx="loop {name}"]', state="attached"
    )
    page.click("[data-lp-star]")
    assert _shown(page) == ["acid bass riff"]
    page.click("[data-lp-star]")
    # tags: a prompt of words, then a chip that filters
    with page.expect_response("**/loop/tag"):
        _row(page, "glass lead").locator(".lp-tagit").click()
        name_it(page, "dark #wide")
    page.wait_for_selector('#loops-panel .lp-tagbar [data-tag="dark"]')
    page.click('#loops-panel .lp-tagbar [data-tag="dark"]')
    assert len(_shown(page)) == 1 and "glass" in _shown(page)[0]
    page.click('#loops-panel .lp-tagbar [data-tag="dark"]')
    page.fill("#lp-q", "#wide")
    assert len(_shown(page)) == 1
    page.fill("#lp-q", "")
    # sort: by tempo, by name — and kept when the list is sent again
    page.click('[data-lp-sort="bpm"]')
    bpms = [
        int(b)
        for b in page.eval_on_selector_all(
            f'{ROWS}[data-what="notes"]',
            "rs => rs.filter(r => r.offsetParent).map(r => r.dataset.bpm)",
        )
    ]
    assert bpms == sorted(bpms) == [96, 124, 132]
    page.click('[data-lp-sort="name"]')
    assert _shown(page) == sorted(_shown(page))
    with page.expect_response("**/loop/star"):  # the region comes back
        acid.locator(".lp-star").click()
    page.wait_for_selector(
        f'{ROWS}[data-ctx="loop {name}"]:not([data-star])', state="attached"
    )
    assert _shown(page) == sorted(_shown(page)) and len(_shown(page)) == 3
    # a press elsewhere folds it (sliding over, it covers the live strip)
    page.locator(".view-head h1").click()
    assert page.evaluate(f"() => {root}") == "folded"
    # ▶ in time: the loop plays at the page's tempo, this one is 132 — a
    # render at the tempo playing
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    page.click("#loops-dock .lp-tab")
    fast = _row(page, "acid bass riff", bpm=132)
    with page.expect_response("**/loop/preview") as answer:
        fast.locator(".lp-play").click()
    url = answer.value.headers["x-preview-url"]
    form = answer.value.request.post_data or ""
    tempo = page.evaluate("() => window.synthlab.tempo()")
    assert url.startswith("/preview/") and f"{tempo:g}" in form
    studio.wait_sound(timeout=15.0)
    page.keyboard.press("Shift+Space")  # stop everything
    # pinned, it is a column
    page.click("#loops-dock [data-lp-pin]")
    assert page.evaluate(f"() => {root}") == "pinned"
    col = page.evaluate(
        "() => getComputedStyle(document.getElementById('view')).gridTemplateColumns"
    )
    assert col.split()[0] in ("320px", "340px"), col
    assert studio.unexplained() == []


def test_a_row_dragged_onto_the_grid_edits_it_and_a_kept_beat_is_a_beat(studio, server):
    _seed(server)
    page = studio.open("perform")
    page.evaluate(
        "() => localStorage.setItem('synthlab.loops',"
        " JSON.stringify({open: true, pin: true}))"
    )
    page.reload()
    page.wait_for_selector("#loops-dock .lp-head")
    studio.settle()
    assert page.evaluate("() => document.documentElement.dataset.loops") == "pinned"
    glass = _row(page, "glass lead")
    name = glass.get_attribute("data-ctx").removeprefix("loop ")
    # dragged onto the note grid: Edit, as this loop
    with page.expect_response("**/loop/edit"):
        glass.drag_to(page.locator("#transport .grid"))
    page.wait_for_selector("#transport .ed-head.is-saved")
    assert server.state.loop_edit_of()["name"] == name
    loop = loops.load(server.data_dir, name)
    cell = '#transport input.cell[name="cell.F#3.0"]'
    assert page.locator(cell).is_checked()
    clear_of_dock(page, cell)
    page.locator(cell).click()
    _wait(page, lambda: loops.load(server.data_dir, name)["edited"], "the edit saved")
    assert ["F#3", 0] not in loops.load(server.data_dir, name)["lane"]["cells"]
    assert loop["lane"]["cells"] != loops.load(server.data_dir, name)["lane"]["cells"]
    with page.expect_response("**/loop/edit/done"):
        page.locator("#transport .lp-done").click()
    # a bank slot onto the drum grid, then ✚ Keep: the grid's beat, a beat
    techno = page.locator(f'{ROWS}[data-kind="bank"]').nth(1)
    with page.expect_response("**/loop/beat"):
        techno.locator(".lp-open").click()
    _wait(page, lambda: bool(server.state.arrangement.drums), "the beat on the grid")
    with page.expect_response("**/loop/beat/keep") as answer:
        page.click("#perform-drums .lp-keep-beat")
    said = json.loads(answer.value.headers["hx-trigger"])["studio-toast"]["message"]
    assert "Kept the beat" in said
    kept = [r for r in loops.listing(server.data_dir) if r["kind"] == "beat"]
    assert len(kept) == 1
    row = page.locator(f'{ROWS}[data-kind="loop"][data-what="beat"]')
    row.wait_for()
    assert row.get_attribute("data-ctx") == f"loop {kept[0]['name']}"
    page.click('[data-lp-kind="beat"]')
    assert row.is_visible()
    # Open puts it back on the drum grid (the notes stay)
    cells = list(server.state.arrangement.cells)
    server.state.arrangement = Arrangement(cells=cells)
    with page.expect_response("**/loop/beat"):
        row.locator(".lp-open").click()
    _wait(page, lambda: bool(server.state.arrangement.drums), "the kept beat opened")
    assert server.state.arrangement.cells == cells
    page.click('[data-lp-kind="all"]')
    assert studio.unexplained() == []


def test_a_sixteen_bar_loop_opens_whole_and_the_roll_scrolls(studio, server):
    """The note grid holds 16 bars at 1/16 (`limits.LOOP_BAR_CHOICES`): Edit
    puts all of a 16-bar loop on Perform's roll, which keeps its step width
    and scrolls rather than squeezing 256 steps into the pane; a note
    clicked in bar 16 saves to the loop."""
    s, d = server.state, server.data_dir
    cells = [["A2", 0], ["C3", 124], ["E3", 252]]
    arr = Arrangement(
        bpm=120, root="A2", scale_name="minor", bars=16, steps_per_bar=16, cells=cells
    )
    payload = s.sound_payload()
    payload["patch"] = {**payload["patch"], "name": "Long lead"}
    name = loops.save(d, "long lead riff", arr, payload)
    page = studio.open("perform")
    page.evaluate(
        "() => localStorage.setItem('synthlab.loops',"
        " JSON.stringify({open: true, pin: true}))"
    )
    page.reload()
    page.wait_for_selector("#loops-dock .lp-head")
    studio.settle()
    with page.expect_response("**/loop/edit"):
        _row(page, "long lead").locator(".lp-edit").click()
    page.wait_for_selector("#transport .ed-head.is-saved")
    studio.settle()
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (16, 16)
    roll = page.locator("#transport .grid.roll")
    assert roll.get_attribute("data-bars") == "16"
    assert page.locator("#transport .grid.roll .ruler b").count() == 16
    # the 16 and 8 chips are on the transport, 16 pressed
    assert page.locator('#transport .barchip[aria-pressed="true"]').inner_text() == "16"
    got = page.evaluate(
        """() => {
          const g = document.querySelector('#transport .grid.roll');
          const a = g.querySelector('input.cell[name="cell.A2.0"]');
          const b = g.querySelector('input.cell[name="cell.A2.1"]');
          const pans = (el) => el.scrollWidth > el.clientWidth + 1
            && getComputedStyle(el).overflowX !== 'visible';
          let p = g;
          while (p && !pans(p)) p = p.parentElement;
          const x = (el) => el.getBoundingClientRect().x;
          return {step: x(b) - x(a), scrolls: !!p,
                  pane: p ? p.clientWidth : 0, wide: p ? p.scrollWidth : 0};
        }"""
    )
    assert got["step"] >= 16 and got["scrolls"] and got["wide"] > got["pane"], got
    # a click in bar 16: on the grid and in the loop
    cell = '#transport input.cell[name="cell.A2.250"]'
    page.locator(cell).scroll_into_view_if_needed()
    clear_of_dock(page, cell)
    page.locator(cell).click()
    _wait(
        page,
        lambda: ["A2", 250] in loops.load(d, name)["lane"]["cells"],
        "the edit saved",
    )
    lane = loops.load(d, name)["lane"]
    assert lane["bars"] == 16 and ["E3", 252] in lane["cells"]
    assert studio.unexplained() == []
    page.goto("about:blank")  # (the 32 s loop's WAV: let its connection go)
