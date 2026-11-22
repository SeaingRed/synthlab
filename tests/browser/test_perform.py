"""Journey 5: Perform — the two step grids, the drum velocity lane,
pattern undo, MIDI out and back in.

Proves: a real click / drag on the grids reaches the server through the
forms' own htmx triggers (each assertion reads the scratch studio's state,
the same `StudioState` the routes wrote), Ctrl+Z reaches the pattern's
undo, and the MIDI link and file input work as a browser drives them.
Does not prove what the loop sounds like.
"""

from __future__ import annotations

import math

NOTE_ROWS = "#transport .grid .gridrow:not(.sliderow)"
DRUM_ROWS = "#perform-drum-grid .drumrow"
DRUM_LANES = "#perform-drum-grid .velrow .vel-lane"


def _posts(page, path: str) -> list:
    seen: list = []
    page.on(
        "request",
        lambda r: r.method == "POST" and r.url.endswith(path) and seen.append(r),
    )
    return seen


def _cells(server) -> set:
    return {tuple(c) for c in server.state.arrangement.cells}


def _drums(server) -> set:
    return {tuple(d) for d in server.state.arrangement.drums}


def _wait(page, check, what: str, timeout: float = 5.0):
    """Poll server-side state the page's request is about to change."""
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


def test_note_and_drum_cells_toggle(studio, server):
    page = studio.open("perform")
    studio.settle()
    before = _cells(server)
    row = page.locator(NOTE_ROWS).nth(0)
    pitch = row.locator(".rowlabel").get_attribute("data-pitch")
    cell = row.locator("input.cell").nth(1)
    assert not cell.is_checked()
    cell.click()
    _wait(page, lambda: _cells(server) == before | {(pitch, 1)}, "the note cell")
    cell.click()
    _wait(page, lambda: _cells(server) == before, "the note cell cleared")

    kick = page.locator(DRUM_ROWS).nth(0).locator("input.cell")
    assert not _drums(server)
    kick.nth(0).click()
    kick.nth(8).click()
    _wait(page, lambda: _drums(server) == {("kick", 0), ("kick", 8)}, "the kicks")
    kick.nth(8).click()
    _wait(page, lambda: _drums(server) == {("kick", 0)}, "one kick cleared")
    studio.settle()
    # the grid on screen is what the server holds
    assert kick.nth(0).is_checked() and not kick.nth(8).is_checked()


def test_the_last_note_and_the_last_hit_can_be_clicked_off(studio, server):
    """Found by this suite: with its last note clicked off the grid posts no
    `cell.*` at all, which the route read as "the grid was not posted" — the
    page showed an empty grid, the loop kept the note, a reload brought it
    back (routes/patch.py `_merge_arrangement`; the drum grid was fine)."""
    page = studio.open("perform")
    studio.settle()
    for pitch, step in sorted(_cells(server)):
        page.locator(f'#transport input.cell[name="cell.{pitch}.{step}"]').click()
    _wait(page, lambda: not _cells(server), "the note grid emptied")
    kick = page.locator(DRUM_ROWS).nth(0).locator("input.cell").nth(0)
    kick.click()
    _wait(page, lambda: _drums(server) == {("kick", 0)}, "a kick")
    kick.click()
    _wait(page, lambda: not _drums(server), "the drum grid emptied")
    studio.settle()
    page.reload()
    page.wait_for_selector("#transport .grid")
    assert page.locator(f"{NOTE_ROWS} input.cell:checked").count() == 0
    assert page.locator(f"{DRUM_ROWS} input.cell:checked").count() == 0
    studio.settle()


def test_drag_painting_the_drum_velocity_lane_posts_the_drum_form(studio, server):
    """Perform has two velocity rows, each in its own form. The stroke's
    `input` used to be fired on the first row found — the note grid's — so
    a drum velocity drag posted the note form and was lost."""
    page = studio.open("perform")
    studio.settle()
    page.locator(DRUM_ROWS).nth(0).locator("input.cell").nth(0).click()
    _wait(page, lambda: _drums(server) == {("kick", 0)}, "a kick to put velocity on")
    studio.settle()

    drum_posts = _posts(page, "/fragment/drums/grid")
    note_posts = _posts(page, "/fragment/arrangement")
    lanes = page.locator(DRUM_LANES)
    a, b = lanes.nth(0).bounding_box(), lanes.nth(3).bounding_box()
    y = a["y"] + a["height"] * 0.5  # half way up the lane: velocity 0.5
    page.mouse.move(a["x"] + a["width"] / 2, y)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] / 2, y, steps=8)
    page.mouse.up()

    vel = lambda: server.state.arrangement.drum_vel  # noqa: E731
    _wait(page, lambda: vel().get("0") == 0.5, "the drum velocities")
    assert [vel().get(str(i)) for i in range(5)] == [0.5, 0.5, 0.5, 0.5, 1.0]
    assert len(drum_posts) == 1 and "dvel.0=0.5" in drum_posts[0].post_data
    assert not note_posts
    assert lanes.nth(0).get_attribute("aria-valuenow") == "0.5"
    studio.settle()


def test_ctrl_z_undoes_the_last_pattern_edit(studio, server):
    page = studio.open("perform")
    studio.settle()
    before = _cells(server)
    row = page.locator(NOTE_ROWS).nth(1)
    pitch = row.locator(".rowlabel").get_attribute("data-pitch")
    row.locator("input.cell").nth(3).click()
    _wait(page, lambda: _cells(server) == before | {(pitch, 3)}, "the edit")
    studio.settle()

    undo = _posts(page, "/fragment/pattern/undo")
    page.keyboard.press("Control+z")
    _wait(page, lambda: _cells(server) == before, "the undo")
    assert len(undo) == 1
    studio.settle()
    # the grid was redrawn from the restored pattern (polled: the answer's
    # morph can land after the network is idle — a known flake, more often
    # since the answer also carries the lane inspector)
    cell = page.locator(NOTE_ROWS).nth(1).locator("input.cell").nth(3)
    _wait(page, lambda: not cell.is_checked(), "the grid redrawn")


def test_midi_export_downloads_and_import_restores_the_notes(studio, server, tmp_path):
    page = studio.open("perform")
    studio.settle()
    row = page.locator(NOTE_ROWS).nth(2)
    pitch = row.locator(".rowlabel").get_attribute("data-pitch")
    row.locator("input.cell").nth(1).click()
    _wait(page, lambda: (pitch, 1) in _cells(server), "a note of our own")
    studio.settle()
    notes = _cells(server)

    with page.expect_download() as dl:
        page.click('a[href="/export/loop.mid"]')
    assert dl.value.suggested_filename.endswith(".mid")
    mid = tmp_path / "loop.mid"
    dl.value.save_as(mid)
    assert mid.read_bytes()[:4] == b"MThd"

    # empty the grid by hand, then bring the file back
    for p, step in sorted(notes):
        page.locator(f'#transport input.cell[name="cell.{p}.{step}"]').click()
    _wait(page, lambda: not _cells(server), "the grid emptied")
    studio.settle()

    page.set_input_files('.midi-io input[type="file"]', mid)
    _wait(page, lambda: _cells(server) == notes, "the imported notes")
    page.wait_for_selector("#toasts .toast")
    assert "note" in page.inner_text("#toasts").lower()
    studio.settle()
    for p, step in notes:
        assert page.locator(
            f'#transport input.cell[name="cell.{p}.{step}"]'
        ).is_checked()


def test_play_names_the_parts_on_and_the_loop_length(studio, server):
    """▶'s cue names what plays from the part switches and the loop's
    length (it read "the loop: its parts that are on")."""
    page = studio.open("perform")
    studio.settle()
    cue = page.locator('[data-cue-for="play"]')
    a = server.state.arrangement
    both = math.lcm(a.bars, a.drum_bars)
    bars = f"{both} bar{'' if both == 1 else 's'}"
    page.wait_for_function(
        "(t) => document.querySelector('[data-cue-for=play]').textContent === t",
        arg=f"the loop · notes + drums · {bars}",
    )
    page.click('.part-btn:has-text("drums")')
    page.wait_for_function(
        "() => document.querySelector('[data-cue-for=play]').textContent"
        ".startsWith('the loop · notes · ')"
    )
    assert "drums" not in cue.text_content()


def test_a_section_launch_shows_its_notes_and_ctrl_z_puts_the_loop_back(studio, server):
    """A section launched on Perform replaces the whole loop, so it must
    not open on an empty octave window ("48 hidden notes") and must be
    undoable: the roll's window moves to its notes, the velocity lane
    stays in view under the roll in Both, and one Ctrl+Z puts back the
    loop as it was: notes, lanes, sound, tempo."""
    from dataclasses import replace

    s = server.state
    with s.lock:
        s.arrangement = replace(
            s.arrangement, root="A1", cells=[["A3", 0], ["C4", 1], ["E4", 2]]
        )
        sid = s.loop_to_section("up high")
        s.arrangement = replace(s.arrangement, bpm=97.0, cells=[["A1", 0]])
        s.patch = s.patch.tweak(cutoff=777.0)
        s.grid_span, s.grid_oct = 1, 0
    page = studio.open("perform")
    # (the Loops panel's "sections" open one as a scene of the launcher,
    # the same one undo step)
    page.evaluate(
        "() => localStorage.setItem('synthlab.loops',"
        " JSON.stringify({open: true, pin: true, kind: 'section'}))"
    )
    page.reload()
    studio.settle()
    row = page.locator('#loops-panel .lp-row[data-what="section"]').first
    assert sid in row.locator(".lp-scene").get_attribute("hx-vals")
    with page.expect_response("**/fragment/perform/scene/section"):
        row.dblclick()  # a row's double-click: its Open (a section: a scene)
    page.wait_for_selector('#transport .rowlabel[data-pitch="E4"]')
    studio.settle()
    shown = page.locator("#transport .grid.roll .gridrow:not(.sliderow) .cell:checked")
    assert shown.count() == 3  # every note in sight, none hidden
    # the velocity lane under the roll, inside the grid's pane
    lane = page.locator(
        "#transport .grid.roll > .velrow:not(.chancerow)"
    ).bounding_box()
    pane = page.locator("#transport .grid.roll").bounding_box()
    assert lane["y"] + lane["height"] <= pane["y"] + pane["height"] + 1, (lane, pane)
    page.locator(".view-head h1").click()
    with page.expect_response("**/fragment/pattern/undo") as answer:
        page.keyboard.press("Control+z")
    assert answer.value.status == 200
    _wait(page, lambda: _cells(server) == {("A1", 0)}, "the loop back")
    assert s.arrangement.bpm == 97.0 and s.patch.cutoff == 777.0
    page.wait_for_selector('#transport .rowlabel[data-pitch="A1"]')
    assert studio.unexplained() == []


_LANE_CLEAR = """(sel) => { const g = document.querySelector(sel);
  const lane = g.querySelector(':scope > .velrow');
  const rows = g.querySelectorAll(':scope > .gridrow'), last = rows[rows.length - 1];
  const scrolls = g.scrollHeight > g.clientHeight + 1;
  const at = (top) => { g.scrollTop = top;
    const foot = last.getBoundingClientRect().bottom;
    return Math.round(foot - lane.getBoundingClientRect().top); };
  const rest = at(0), end = at(g.scrollHeight);
  g.scrollTop = 0;
  return { scrolls, rest, end }; }"""


def test_the_velocity_lane_hides_no_row_the_pane_can_show(studio, server):
    """The velocity lane sticks to the foot of the roll's pane and the
    drum grid's. The roll's rows must share the pane less the chord lane's
    row too (as well as a lane, ruler and slide row), or at 1920 in Both
    they overflow by its height and the lane hides the slide row and the
    bottom note row until the pane is scrolled. Rows
    that fit now end above the lane; rows that cannot fit scroll, and the
    last one clears it at the end of the scroll."""
    with server.state.lock:
        server.state.grid_span, server.state.grid_oct = 2, 0
    page = studio.page
    for w, h in ((1920, 1080), (2560, 1400)):
        page.set_viewport_size({"width": w, "height": h})
        studio.open("perform")
        studio.settle()
        page.locator('[data-rack-show="both"]').first.click()
        page.wait_for_timeout(200)
        for sel in ("#transport .grid.roll", "#perform-drum-grid .drum-grid-8"):
            got = page.evaluate(_LANE_CLEAR, sel)
            assert got["end"] <= 1, (w, sel, got)
            assert got["scrolls"] or got["rest"] <= 1, (w, sel, got)
            if sel.startswith("#transport"):
                assert not got["scrolls"], (w, got)  # two octaves fit in Both
    assert studio.unexplained() == []


def test_the_chance_lane_takes_the_velocity_lanes_place_and_writes_the_chance(
    studio, server
):
    """The roll's chance lane is the velocity lane's
    switch — its label flips the two (one row at a time, so the rows keep
    their height), a drag paints a step's chance, PageDown from a velocity
    bar lands on the chance bar of the same step and ↓ lowers it; the
    grid's post stores only what is under 100 %, and the loop's schedule
    (the MIDI file's) then holds fewer notes. Not how the loop sounds."""
    from dataclasses import replace

    from synthlab.studio.arrange import note_schedule

    cells = [["A1", st] for st in range(0, 16, 2)]
    with server.state.lock:
        s = server.state
        s.arrangement = replace(
            s.arrangement, steps_per_bar=16, bars=1, cells=cells, note_chance={}
        )
    page = studio.open("perform")
    studio.settle()
    vel = "#transport .grid.roll > .velrow:not(.chancerow)"
    chance = "#transport .grid.roll > .chancerow"
    assert page.locator(vel).is_visible() and not page.locator(chance).is_visible()
    page.click(f"{vel} .lane-flip")
    assert page.locator(chance).is_visible() and not page.locator(vel).is_visible()
    # the mouse: steps 2 to 4 low
    bars = page.locator(f"{chance} .vel-lane")
    a, z = bars.nth(2).bounding_box(), bars.nth(4).bounding_box()
    y = a["y"] + a["height"] * 0.9
    page.mouse.move(a["x"] + 2, y)
    page.mouse.down()
    page.mouse.move(z["x"] + z["width"] - 2, y, steps=6)
    page.mouse.up()
    _wait(
        page,
        lambda: {"2", "4"} <= set(server.state.arrangement.note_chance),
        "the painted chance",
    )
    assert all(v < 0.3 for v in server.state.arrangement.note_chance.values())
    # the keys: back to the velocities (the label), PageDown on step 9, ↓ x 5
    page.click(f"{chance} .lane-flip")
    assert page.locator(vel).is_visible()
    page.locator(f"{vel} .vel-lane").nth(8).focus()
    page.keyboard.press("PageDown")
    focused = page.evaluate("() => document.activeElement.getAttribute('aria-label')")
    assert focused == "chance step 9" and page.locator(chance).is_visible()
    for _ in range(5):
        page.keyboard.press("ArrowDown")
    _wait(
        page,
        lambda: server.state.arrangement.note_chance.get("8") == 0.75,
        "the keyed chance",
    )
    page.keyboard.press("PageUp")
    assert page.locator(vel).is_visible()
    assert len(note_schedule(server.state.arrangement)[0]) < len(cells)
    assert studio.unexplained() == []


def _one_line(page, sel: str) -> bool:
    """Every visible child of `sel` on one line: each overlaps every other
    vertically (a second line starts under the first one's foot)."""
    return page.evaluate(
        """(sel) => {
          const rs = [...document.querySelector(sel).children]
            .filter((c) => c.checkVisibility() && c.getBoundingClientRect().height > 0)
            .map((c) => c.getBoundingClientRect());
          const top = Math.max(...rs.map((r) => r.top));
          return top < Math.min(...rs.map((r) => r.bottom)); }""",
        sel,
    )


def test_the_toolbars_are_one_line_and_tools_holds_the_rest_its_keys_work_shut(
    studio, server
):
    """The note grid's toolbar and the controls row over it (the key, the
    feel) are one line each at 1920 x 1080, dense and comfy; the rest of the
    tools are Tools ▾'s — shut, a command from the palette and a roll key
    (Ctrl+D) still reach them, and open, an action leaves it open."""
    from dataclasses import replace

    server.state.arrangement = replace(server.state.arrangement, cells=[], cuts=[])
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    for density in ("compact", "comfy"):
        page.context.add_cookies(
            [{"name": "density", "value": density, "url": server.url}]
        )
        studio.open("perform")
        studio.settle()
        for sel in (
            "#transport .transport-controls",
            "#transport .pattern-tools",
            "#perform-drums .pattern-tools",
        ):
            assert _one_line(page, sel), (density, sel)
    assert not page.locator("#ptm-notes").is_visible()
    row = page.locator(NOTE_ROWS).nth(0)
    pitch = row.locator(".rowlabel").get_attribute("data-pitch")
    row.locator("input.cell").nth(0).click()
    _wait(page, lambda: (pitch, 0) in _cells(server), "the note")
    # shut: the palette runs a tool that lives in it
    page.locator(".view-head h1").click()
    page.keyboard.press("Control+k")
    page.fill("#pal-q", "Shift notes right")
    with page.expect_response("**/fragment/pattern/notes/shift_right"):
        page.keyboard.press("Enter")
    _wait(page, lambda: _cells(server) == {(pitch, 1)}, "the note a step later")
    # shut: a roll key (Ctrl+D, the selection duplicated: roll.js presses its button)
    page.click('#transport [data-roll-tool="select"]')
    row.locator("input.cell").nth(1).click()
    with page.expect_response("**/fragment/pattern/notes/copy"):
        page.keyboard.press("Control+d")
    _wait(page, lambda: _cells(server) == {(pitch, 1), (pitch, 2)}, "the copy")
    assert not page.locator("#ptm-notes").is_visible()
    page.click('#transport [data-roll-tool="draw"]')
    # open: an action from it, and it stays open for the next
    page.click("#transport .pt-more-btn")
    page.wait_for_selector("#ptm-notes:popover-open")
    with page.expect_response("**/fragment/pattern/notes/down"):
        page.click('#ptm-notes [data-cmd="Transpose down a scale tone"]')
    studio.settle()
    page.wait_for_selector("#ptm-notes:popover-open")
    assert len({c[0] for c in _cells(server)}) == 2  # (the selected copy moved down)
    page.keyboard.press("Escape")
    page.wait_for_selector("#ptm-notes:not(:popover-open)", state="attached")
    assert studio.unexplained() == []


def test_play_opens_from_the_keys_dock_and_its_keys_play_it_shut(studio, server):
    """The right column is the lane inspector alone; Play (pads, XY, FX,
    beats) opens from the keyboard dock's Tools ("Pads ▴"), over the page
    above the dock's right end, one of it and Play tools at a time, and
    again on the next visit; its pads' keys play them shut and open."""
    page = studio.open("perform")
    studio.settle()
    side = page.evaluate(
        "() => [...document.querySelector('.perform-side').children]"
        ".filter((c) => c.matches('.module')).map((c) => c.id)"
    )
    assert side == ["perform-lane"]
    assert page.locator("#perform-slots #perform-arrange .to-song").is_visible()
    play, btn = page.locator("#perform-play"), page.locator("#keys-dock #play-open")
    assert not play.is_visible() and btn.is_visible()
    # typing → pads: Q strikes the kick pad, Play shut
    page.click('#kbd-typeto [data-typeto="pads"]')
    page.locator(".view-head h1").click()
    with page.expect_request("**/drum/kick/hit.wav*"):
        page.keyboard.press("KeyQ")
    # Pads ▴ opens it over the page, above the dock, at its right end
    btn.click()
    page.wait_for_selector("#perform-play:popover-open")
    page.wait_for_selector('#play-open[aria-expanded="true"]')
    box, dock = play.bounding_box(), page.locator("#keys-dock").bounding_box()
    assert box["y"] + box["height"] <= dock["y"] + 1, (box, dock)
    assert box["x"] + box["width"] > dock["x"] + dock["width"] - 40, (box, dock)
    # one of it and Play tools at a time
    page.click("#keys-dock .play-tools-btn")
    page.wait_for_selector("#play-tools:popover-open")
    assert not play.is_visible()
    page.click("#keys-dock .play-tools-btn")
    page.wait_for_selector("#play-tools:not(:popover-open)", state="attached")
    btn.click()
    page.wait_for_selector("#perform-play:popover-open")
    # another tab shown: W still strikes the snare
    page.click('#perform-play [data-tab-pick="fx"]')
    page.locator(".view-head h1").click()
    with page.expect_request("**/drum/snare/hit.wav*"):
        page.keyboard.press("KeyW")
    assert play.is_visible()
    # elsewhere there is no Pads ▴; back on the page, Play opens as it was left
    studio.nav("drums")
    studio.settle()
    assert not btn.is_visible()
    studio.nav("perform")
    studio.settle()
    page.wait_for_selector("#perform-play:popover-open")
    page.click("#perform-play .play-x")
    page.wait_for_selector("#perform-play:not(:popover-open)", state="attached")
    page.wait_for_selector('#play-open[aria-expanded="false"]')
    page.evaluate("() => localStorage.setItem('synthlab.typeto', 'keys')")
    assert studio.unexplained() == []
