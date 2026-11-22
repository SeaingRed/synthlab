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


def _tools(page, sel: str) -> None:
    """A tool in the note grid's Tools ▾: opened, pressed, shut again (before
    the answer, so it does not open again over the grid)."""
    page.click("#transport .pt-more-btn")
    page.locator(f"#ptm-notes {sel}").click()
    page.evaluate("() => document.getElementById('ptm-notes').hidePopover()")


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
    # (a cell's click selects it: its loop opened on its lane, launched)
    page.locator(f"{_slot(0, 0)} .sl-slot").click()
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
        _tools(page, '[hx-post="/fragment/pattern/notes/shift_right"]')
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
    # ■ lane 1 (its head's): it stops at its point, its ■ pressed
    stop = page.locator("#slots .sl-heads .sl-lane .sl-stop").nth(0)
    with page.expect_response("**/fragment/perform/slot/stop"):
        stop.click()
    page.wait_for_function(SWITCHED, timeout=8000)
    _wait(page, lambda: s.layers[0].get("muted") is True, "lane 1 stopped")
    _wait(page, lambda: stop.get_attribute("aria-pressed") == "true", "■ pressed")
    page.click("#play")
    assert studio.unexplained() == []


def test_a_cells_click_selects_it_and_the_editor_shows_it(studio, server):
    """One selection: a filled cell's click (or Enter on it) selects it and
    opens its loop on its lane — queued while the loop plays, then playing
    — and the editor's head names it, saved; a drum cell turns the editor
    to the drums, whose head names the beat; an empty cell's click makes
    its lane a sketch ("not saved", Save as loop)."""
    _seed(server)
    page = _open(studio)
    s = server.state
    _drop(page, "acid line", 0, 0)
    _drop(page, "pluck hook", 0, 1)
    _drop(page, "techno beat", 4, 1)
    page.click("#play")
    studio.wait_sound()
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.locator(f"{_slot(0, 1)} .sl-slot").click()
    page.locator(f'{_slot(0, 1)}[aria-selected="true"]').wait_for()
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(0, 1)}[data-state="playing"]').wait_for()
    assert s.loop_edit_of(0)["name"] == "Pluck hook"
    page.wait_for_selector("#transport .ed-head.is-saved")
    assert "Pluck hook" in page.locator("#transport .ed-head .ed-name").inner_text()
    # Enter on another: selected and opened
    page.locator(f"{_slot(0, 0)} .sl-slot").focus()
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.keyboard.press("Enter")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(
        f'{_slot(0, 0)}[data-state="playing"][aria-selected="true"]'
    ).wait_for()
    assert s.loop_edit_of(0)["name"] == "Acid line"
    # the drums' cell: the editor turns to the drums, which name the beat
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.locator(f"{_slot(4, 1)} .sl-slot").click()
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(4, 1)}[data-state="playing"]').wait_for()
    page.wait_for_selector("#perform-drums .ed-head.is-saved")
    assert page.evaluate("() => document.documentElement.dataset.rack") == "drums"
    studio.wait_sound()
    # an empty cell of lane 1: its lane a sketch, saved to no loop
    with page.expect_response("**/fragment/perform/slot/select"):
        page.locator(f"{_slot(0, 2)} .sl-empty").click()
    page.wait_for_selector("#transport .ed-head:not(.is-saved) [data-save-sketch]")
    assert s.loop_edit_of(0) is None and s.slot_at(0, 0) == "Acid line"
    # Save as loop: into the selected empty cell, which then plays it
    with page.expect_response("**/fragment/perform/slot/keep"):
        page.click("#transport [data-save-sketch]")
    page.locator(f'{_slot(0, 2)}[data-state="playing"]').wait_for()
    page.wait_for_selector("#transport .ed-head.is-saved")
    page.click("#play")
    assert studio.unexplained() == []


def test_select_a_cell_play_it_alone_on_the_deck_and_rec_into_it(studio, server):
    """The journey: a cell selected is what the editor shows; the deck's
    Loop ▶ plays its loop alone (and Now playing says so); an edit saves to
    it and is heard alone again; the deck's ⏺ Rec — the dock's, lit
    together — overdubs the take into it, as its ▾ says."""
    _seed(server)
    page = _open(studio)
    s = server.state
    page.evaluate(
        """() => { localStorage.setItem('synthlab.keyRecBars', '1');
          localStorage.setItem('synthlab.keyRecCount', '0');
          localStorage.setItem('synthlab.keyRecHow', 'overdub'); }"""
    )
    page.reload()  # (keyrec.js reads its settings once)
    page.wait_for_selector("#slots")
    studio.settle()
    _drop(page, "acid line", 0, 0)
    with page.expect_response("**/fragment/perform/slot/launch"):
        page.locator(f"{_slot(0, 0)} .sl-slot").click()
    page.wait_for_selector("#transport .ed-head.is-saved")
    assert "Acid line" in page.locator("#transport .ed-head .ed-name").inner_text()
    # the deck's Loop (the default): ▶ plays the cell's loop alone
    assert page.get_attribute('[data-deck-src="loop"]', "aria-pressed") == "true"
    with page.expect_response("**/fragment/perform/slot/hear") as heard:
        page.click("#play")
    assert heard.value.ok
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    studio.wait_sound()
    page.wait_for_function(
        "() => document.querySelector('#now-playing .np-label')"
        ".textContent.includes('alone')"
    )
    assert "Acid line" in page.locator("#now-playing .np-label").inner_text()
    assert page.get_attribute("#play", "aria-pressed") == "true"
    page.wait_for_selector('#perform-transport .deck-meter > i[data-s="now"]')
    # an edit saves to its loop, and the deck asks for it alone again
    with page.expect_response("**/fragment/perform/slot/hear"):
        _tools(page, '[hx-post="/fragment/pattern/notes/shift_right"]')
    _wait(
        page,
        lambda: ["A2", 1] in loops.load(server.data_dir, "Acid line")["lane"]["cells"],
        "the edit saved to the cell's loop",
    )
    # Rec ▾ says where the take lands: the selected cell, overdubbed
    page.click("#perform-transport .deck-rec-more")
    page.wait_for_selector("#deck-rec-menu:popover-open")
    assert "overdub" in page.locator("#deck-rec-menu .deck-lands").inner_text()
    page.keyboard.press("Escape")
    # the deck's ⏺ Rec: the dock's lights with it; C4 into the cell's loop
    page.locator(".view-head h1").click()
    page.click("#deck-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    page.wait_for_function(
        "() => document.getElementById('deck-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    page.keyboard.down("KeyA")
    page.wait_for_timeout(400)
    page.keyboard.up("KeyA")
    with page.expect_response("**/take/keys", timeout=20000) as answer:
        pass  # one bar: it stops by itself
    assert answer.value.ok
    _wait(
        page,
        lambda: any(
            c[0] == "C4"
            for c in loops.load(server.data_dir, "Acid line")["lane"]["cells"]
        ),
        "the take overdubbed into the cell's loop",
    )
    assert s.slot_at(0, 0) == "Acid line"
    page.wait_for_selector('#deck-rec[aria-pressed="false"]')
    # ▶ again is its own stop
    page.click("#play")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
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
        "free": 2,  # (the lane's next empty row: itself, empty)
        "playing": False,
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
        row.dblclick()  # a row's double-click: its Open (a section: a scene)
    page.locator('#slots .sl-row[data-row="1"] .sl-scene small').wait_for()
    assert s.slot_rows()[1]["name"] == "Verse"
    assert s.slot_rows()[1]["slots"][0] and s.slot_rows()[1]["slots"][4]
    page.click('[data-lp-kind="all"]')
    assert studio.unexplained() == []
