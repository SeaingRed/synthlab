"""Perform's launcher in Chromium.

What these journeys prove: Loops panel rows dragged onto slots fill them (a
beat only in the drums); a slot launched while the loop plays waits for
its point (queued) and then plays, and the master carries sound; Alt+2
launches a scene; an edit of the playing slot's lane saves to its loop;
Ctrl+Z takes a scene launch back; ■ stops a lane; the grid's keys (arrows,
Shift+Enter, Delete) and the quantise choice (kept per browser); an empty
slot's ＋ keeps the lane; a song section opens from the panel as a scene.
Levels are the page's analyser's — nobody has listened: whether a launch
lands on the bar line by ear, and the sound, are unchecked.
"""

from __future__ import annotations

import time

from synthlab.studio import loops
from synthlab.studio.state.model import Arrangement
from synthlab.synth import Patch

QUEUED = "() => !!document.querySelector('#slots .sl-cell[data-q]')"
SWITCHED = "() => !document.querySelector('#slots .sl-cell[data-q]')"


def _wait(page, check, what: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _seed(server) -> None:
    """Two loops of notes in two sounds, and a beat."""
    d, s = server.data_dir, server.state
    for name, sound, cells in (
        ("Acid line", "Acid bass", [["A2", 0], ["C3", 4], ["E3", 8]]),
        ("Pluck hook", "Glass pluck", [["E3", 0], ["G3", 6]]),
    ):
        arr = Arrangement(bpm=124.0, bars=1, steps_per_bar=16, cells=cells)
        pay = {
            "format": 2,
            "patch": Patch(name=sound, cutoff=2000.0).to_json(),
            "layers": [],
            "main_fx": [],
            "curves": {},
        }
        loops.save(d, name, arr, pay)
        time.sleep(0.01)
    loops.save_beat(d, "Techno beat", s.drum_bank[0], Arrangement(bpm=124.0))


def _open(studio):
    page = studio.open("perform")
    page.evaluate(
        "() => localStorage.setItem('synthlab.loops',"
        " JSON.stringify({open: true, pin: true}))"
    )
    page.reload()
    page.wait_for_selector("#slots")
    studio.settle()
    return page


def _slot(lane: int, row: int) -> str:
    return f'#slots .sl-cell[data-lane="{lane}"][data-row="{row}"]'


def _drop(page, title: str, lane: int, row: int) -> None:
    src = page.locator(f'#loops-panel .lp-row[data-drag-loop][data-title="{title}"]')
    with page.expect_response("**/fragment/perform/slot/place"):
        src.drag_to(page.locator(_slot(lane, row)))
    page.locator(f"{_slot(lane, row)} .sl-slot:not(.sl-empty)").wait_for()


def test_fill_launch_a_slot_and_a_scene_edit_undo_and_stop(studio, server):
    _seed(server)
    page = _open(studio)
    _drop(page, "acid line", 0, 0)
    _drop(page, "pluck hook", 0, 1)
    _drop(page, "techno beat", 4, 1)
    # a beat goes in the drums only: nothing happens on a lane of notes
    assert page.locator(f"{_slot(1, 0)} .sl-empty").count() == 1
    s = server.state
    assert [r["slots"] for r in s.slot_rows()] == [
        ["Acid line", None, None, None, None],
        ["Pluck hook", None, None, None, "Techno beat"],
    ]
    # play, then launch a slot: it waits for the bar, then plays
    page.click("#play")
    studio.wait_sound()
    # (a filled slot's click selects it; its ▶ launches)
    page.locator(f"{_slot(0, 0)} .sl-go").click()
    page.wait_for_function(QUEUED)
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(0, 0)}[data-state="playing"]').wait_for()
    assert s.loop_edit_of(0)["name"] == "Acid line"
    assert sorted(map(tuple, s.arrangement.cells)) == [("A2", 0), ("C3", 4), ("E3", 8)]
    studio.wait_sound()
    # Alt+2: scene 2 — its two slots
    page.locator(".view-head h1").click()
    with page.expect_response("**/fragment/perform/scene/launch"):
        page.keyboard.press("Alt+2")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(4, 1)}[data-state="playing"]').wait_for()
    assert page.locator('#slots .sl-row[data-row="1"].is-on').count() == 1
    assert {k: e["name"] for k, e in s.loop_edit.items()} == {
        0: "Pluck hook",
        4: "Techno beat",
    }
    # an edit of the playing slot's lane saves to its loop
    with page.expect_response("**/fragment/pattern/notes/shift_right"):
        page.locator('[hx-post="/fragment/pattern/notes/shift_right"]').first.click()
    _wait(
        page,
        lambda: (
            loops.load(server.data_dir, "Pluck hook")["lane"]["cells"]
            == [["E3", 1], ["G3", 7]]
        ),
        "the playing slot's loop saved",
    )
    # Ctrl+Z: the shift, then the scene launch — scene 1's slot plays again
    page.locator(".view-head h1").click()
    for _ in range(2):
        with page.expect_response("**/fragment/pattern/undo"):
            page.keyboard.press("Control+z")
    page.locator(f'{_slot(0, 0)}[data-state="playing"]').wait_for()
    assert s.loop_edit_of(4) is None
    # ■ lane 1: it stops at its point, its ■ pressed
    stop = page.locator("#slots .sl-stop").nth(0)
    with page.expect_response("**/fragment/perform/slot/stop"):
        stop.click()
    page.wait_for_function(SWITCHED, timeout=8000)
    _wait(page, lambda: s.layers[0].get("muted") is True, "lane 1 stopped")
    _wait(page, lambda: stop.get_attribute("aria-pressed") == "true", "■ pressed")
    page.click("#play")
    assert studio.unexplained() == []


def test_a_slots_click_selects_and_play_double_click_or_enter_launch(studio, server):
    """Deliberately, a filled slot's click only selects it
    (its lane edited, nothing queued, nothing plays); its ▶, a double-click
    or Enter on the selected slot launches it at the quantise point."""
    _seed(server)
    page = _open(studio)
    s = server.state
    _drop(page, "acid line", 0, 0)
    _drop(page, "pluck hook", 0, 1)
    _drop(page, "techno beat", 4, 1)
    page.click("#play")
    studio.wait_sound()
    # a click: selected, no launch
    with page.expect_response("**/fragment/perform/slot/select"):
        page.locator(f"{_slot(0, 1)} .sl-slot").click()
    page.locator(f'{_slot(0, 1)}[aria-selected="true"]').wait_for()
    page.wait_for_timeout(400)
    assert page.locator("#slots .sl-cell[data-q]").count() == 0
    assert s.slot_playing(0) is None and not s.loop_edit
    # Enter on the selected slot: it launches (queued, then playing)
    page.locator(f"{_slot(0, 1)} .sl-slot").focus()
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.keyboard.press("Enter")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(0, 1)}[data-state="playing"]').wait_for()
    assert s.loop_edit_of(0)["name"] == "Pluck hook"
    # a double-click on another: it launches
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.locator(f"{_slot(0, 0)} .sl-slot").dblclick()
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(
        f'{_slot(0, 0)}[data-state="playing"][aria-selected="true"]'
    ).wait_for()
    assert s.loop_edit_of(0)["name"] == "Acid line"
    # the drums' ▶: it launches the beat
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.locator(f"{_slot(4, 1)} .sl-go").click()
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(4, 1)}[data-state="playing"]').wait_for()
    studio.wait_sound()
    page.click("#play")
    assert studio.unexplained() == []


def test_keys_quantise_keep_and_a_section_as_a_scene(studio, server):
    _seed(server)
    s = server.state
    with s.lock:
        s.arrangement = Arrangement(
            bpm=124.0,
            bars=1,
            steps_per_bar=16,
            cells=[["A1", 0], ["A1", 8]],
            drums=[["kick", 0], ["kick", 8]],
        )
        sid = s.loop_to_section("Verse")
    page = _open(studio)
    # the quantise: 4 bars, kept per browser
    page.click('[data-sl-q="4"]')
    page.reload()
    page.wait_for_selector("#slots")
    assert page.locator('[data-sl-q="4"]').get_attribute("aria-pressed") == "true"
    page.click('[data-sl-q="1"]')
    # an empty slot's click selects it (where ⏺ Rec lands) — a
    # second click lets it go; nothing is kept
    empty = page.locator(f"{_slot(1, 2)} .sl-empty")
    with page.expect_response("**/fragment/perform/slot/select"):
        empty.click()
    page.locator(f'{_slot(1, 2)}[aria-selected="true"]').wait_for()
    assert page.evaluate("() => window.synthlab.slotSel()") == {
        "lane": 1,
        "row": 2,
        "kind": "notes",
        "filled": False,
        "name": "",
    }
    empty.click()
    page.locator(f'{_slot(1, 2)}[aria-selected="false"]').wait_for()
    assert page.evaluate("() => window.synthlab.slotSel()") is None
    assert s.perform_slots is None
    # an empty slot's ＋ keeps what lane 1 plays, and it is the playing slot
    with page.expect_response("**/fragment/perform/slot/keep"):
        page.locator(f"{_slot(0, 0)} .sl-plus").click()
    kept_on = f'{_slot(0, 0)}[data-state="playing"][aria-selected="true"]'
    page.locator(kept_on).wait_for()
    kept = s.slot_at(0, 0)
    assert sorted(map(tuple, loops.load(server.data_dir, kept)["lane"]["cells"])) == [
        ("A1", 0),
        ("A1", 8),
    ]
    # the grid's keys: the roving focus, Delete stops the lane
    page.locator(f"{_slot(0, 0)} .sl-slot").focus()
    page.keyboard.press("ArrowRight")
    assert (
        page.evaluate("() => document.activeElement.closest('.sl-cell').dataset.lane")
        == "1"
    )
    assert page.locator("#slots .sl-slot[tabindex='0']").count() == 1
    page.keyboard.press("ArrowLeft")
    with page.expect_response("**/fragment/perform/slot/stop"):
        page.keyboard.press("Delete")
    _wait(page, lambda: s.layers[0].get("muted") is True, "Delete stopped the lane")
    # a song section, from the panel's "sections", as a scene of new slots
    page.click('[data-lp-kind="section"]')
    row = page.locator('#loops-panel .lp-row[data-what="section"]')
    assert row.count() == 1 and sid in row.locator(".lp-scene").get_attribute("hx-vals")
    with page.expect_response("**/fragment/perform/scene/section"):
        row.locator(".lp-scene").click()
    page.locator('#slots .sl-row[data-row="1"] .sl-scene small').wait_for()
    assert s.slot_rows()[1]["name"] == "Verse"
    assert s.slot_rows()[1]["slots"][0] and s.slot_rows()[1]["slots"][4]
    page.click('[data-lp-kind="all"]')
    assert studio.unexplained() == []
