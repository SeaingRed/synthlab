"""Journey 6: Song — a loop becomes a section, a cell gets an automation
lane (the level, a parameter of an FX unit), the export form downloads, a
cell is dragged onto another (moved; Ctrl copies).

Proves: the Song page's forms work as a browser drives them (htmx posts,
the OOB redraws, plain GET downloads), the lane reaches the song model,
and the export's files are what their names say (magic bytes, the LUFS in
the name). Does not prove the song plays, or that a lane is audible.
"""

from __future__ import annotations

import re

GAIN = '.lane-row[data-target="gain"]'


def _with_a_beat(server) -> None:
    """A beat in the loop: "Add loop to song" brings a beat only when the
    loop has one (not an empty, silent beat)."""
    from dataclasses import replace

    from synthlab.studio import beatgen

    a = server.state.arrangement
    drums = beatgen.generate("four-floor", 1, a.drum_bars, a.drum_steps)
    server.state.arrangement = replace(a, drums=drums)


def _song_with_a_section(studio, server):
    _with_a_beat(server)
    page = studio.open("song")
    studio.settle()
    assert not server.state.song["sections"]
    page.click('button[hx-post="/song/from-loop"]')  # "Add loop to song"
    page.wait_for_selector(".arr-cell.filled")
    studio.settle()
    assert len(server.state.song["sections"]) == 1
    return page


def _open_first_cell(studio, page):
    page.locator(".arr-cell.filled .cell-btn").first.click()
    page.wait_for_selector("#song-drawer .lane-add")
    studio.settle()


def _lanes(server) -> dict:
    return server.state.song["sections"][0].get("auto", {})


def test_a_cell_gets_a_gain_lane_and_its_marker(studio, server):
    page = _song_with_a_section(studio, server)
    _open_first_cell(studio, page)
    assert page.locator(".cell-auto").count() == 0

    page.select_option(".lane-add select", "gain")
    page.click(".lane-add button[type=submit]")
    page.wait_for_selector(GAIN)
    studio.settle()
    (lanes,) = _lanes(server).values()
    assert lanes == [{"target": "gain", "points": [[0.0, 0.5], [1.0, 0.5]]}]
    assert page.locator(".arr-cell .cell-auto").count() == 1  # the ∿ mark

    end = page.locator(f'{GAIN} input[name="end"]')
    end.fill("0.75")  # a slider move: posts on change
    page.wait_for_function(
        '() => document.querySelector(\'.lane-row[data-target="gain"] '
        "input[name=\"end\"]')?.getAttribute('value') === '0.75'"
    )
    (lanes,) = _lanes(server).values()
    assert lanes[0]["points"] == [[0.0, 0.5], [1.0, 0.75]]
    assert "150%" in page.locator(f"{GAIN} output").last.inner_text()
    assert page.locator(".arr-cell .cell-auto").count() == 1

    page.click(f"{GAIN} button.danger")  # remove the lane: the mark goes
    page.wait_for_function("() => !document.querySelector('.cell-auto')")
    assert not _lanes(server)
    studio.settle()


def test_keyboard_focus_survives_a_lane_slider_change(studio, server):
    """Each change redraws the whole drawer. As an OOB outerHTML swap that
    destroyed the focused slider: focus fell to <body> and a second arrow
    press moved nothing (found by this suite). The drawer now comes back as
    a morph, which keeps the node — and its focus."""
    page = _song_with_a_section(studio, server)
    _open_first_cell(studio, page)
    page.select_option(".lane-add select", "gain")
    page.click(".lane-add button[type=submit]")
    page.wait_for_selector(GAIN)
    studio.settle()

    page.locator(f'{GAIN} input[name="end"]').focus()
    for want in (0.51, 0.52, 0.53):
        page.keyboard.press("ArrowRight")
        page.wait_for_function(
            '(v) => document.querySelector(\'.lane-row[data-target="gain"] '
            "input[name=\"end\"]')?.getAttribute('value') === v",
            arg=str(want),
        )
        studio.settle()
        focused = page.evaluate(
            "[document.activeElement.tagName, document.activeElement.name,"
            " !!document.activeElement.closest('.lane-row[data-target=\"gain\"]')]"
        )
        assert focused == ["INPUT", "end", True], (want, focused)
    (lanes,) = _lanes(server).values()
    assert lanes[0]["points"][-1] == [1.0, 0.53]


def _drawer_steps(page) -> list[str]:
    """The drawer grid's lit steps, as the live checkboxes say."""
    return page.evaluate(
        "[...document.querySelectorAll('#song-drawer .grid input.cell')]"
        ".filter((c) => c.checked && !c.name.startsWith('slide.'))"
        ".map((c) => c.name).sort()"
    )


def test_the_drawer_follows_the_cell_and_esc_closes_it(studio, server):
    """The drawer is morphed, not replaced, so one set of nodes is redrawn
    as a drum grid, then a note grid, then a drum grid again: each time the
    live checkboxes must be the opened pattern's (an edit made by hand
    included), and Esc must close it. Esc used to reopen it: song.js's
    empty post carried the open drawer's own hidden section_id / track_id
    (htmx.ajax posts every named input on the page) — found by this suite."""
    page = _song_with_a_section(studio, server)
    cells = page.locator(".arr-cell.filled .cell-btn")
    assert cells.count() == 2  # the loop's beat and its synth lane

    def truth() -> tuple[str, list[str]]:
        kind = page.get_attribute("#song-drawer", "data-kind")
        pid = page.get_attribute("#song-drawer", "data-pattern-id")
        p = server.state.song["patterns"][pid]  # read fresh: edits replace it
        if kind == "drums":
            return kind, sorted(f"drum.{v}.{i}" for v, i in p["drums"])
        return kind, sorted(f"cell.{n}.{i}" for n, i in p["cells"])

    def open_cell(i: int, kind: str) -> None:
        cells.nth(i).click()
        page.wait_for_selector(f'#song-drawer:not([hidden])[data-kind="{kind}"]')
        studio.settle()

    open_cell(0, "drums")
    assert truth() == ("drums", _drawer_steps(page))
    want = sorted({*truth()[1]} ^ {"drum.snare.4"})  # the beat, the step flipped
    page.locator('#song-drawer input.cell[name="drum.snare.4"]').click()
    for _ in range(100):  # the grid posts 150 ms after the click
        if truth()[1] == want:
            break
        page.wait_for_timeout(50)
    assert truth()[1] == want

    open_cell(1, "notes")
    kind, notes = truth()
    assert notes and (kind, notes) == ("notes", _drawer_steps(page))

    open_cell(0, "drums")  # back: the hand-made snare is drawn, lit
    assert truth() == ("drums", want) == ("drums", _drawer_steps(page))

    page.keyboard.press("Escape")
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    assert server.state.song_drawer is None
    assert page.locator("#song-drawer > *").count() == 0
    studio.settle()


def test_export_wav_then_flac_at_streaming_loudness_and_measure(
    studio, server, tmp_path
):
    page = _song_with_a_section(studio, server)

    with page.expect_download() as dl:  # the form's defaults: as played, WAV
        page.click(".song-export button[type=submit]")
    assert dl.value.suggested_filename == "synthlab-song.wav"
    wav = tmp_path / "song.wav"
    dl.value.save_as(wav)
    assert wav.read_bytes()[:4] == b"RIFF"

    # the format and the loudness are under Export's ▾
    page.click('[popovertarget="song-export-pop"]')
    page.select_option('.song-export select[name="format"]', "flac")
    page.select_option("#song-loudness-target", "streaming")
    with page.expect_download() as dl:
        page.click(".song-export button[type=submit]")
    name = dl.value.suggested_filename
    assert re.fullmatch(r"synthlab-song \(-1[34]\.\d LUFS\)\.flac", name), name
    flac = tmp_path / "song.flac"
    dl.value.save_as(flac)
    assert flac.read_bytes()[:4] == b"fLaC"

    page.click('[popovertarget="song-export-pop"]')  # ⤓ Export shut it
    page.click('.song-export button[hx-get="/export/song/loudness"]')  # measure
    page.wait_for_function(
        "() => /LUFS/.test(document.getElementById('song-loudness')?.textContent)"
    )
    readout = page.inner_text("#song-loudness")
    assert re.search(r"-1[34]\.\d LUFS", readout) and "true peak" in readout
    studio.settle()


# --- undo / redo, the solo audition, the arranger's cursor ---------------------------


def _cell_ids(server) -> tuple[str, str, str]:
    """(section id, the drums track's id, the synth track's id)."""
    song = server.state.song
    kinds = {t["kind"]: t["id"] for t in song["tracks"]}
    return song["sections"][0]["id"], kinds["drums"], kinds["synth"]


def _cells(server) -> dict:
    return dict(server.state.song["sections"][0]["cells"])


def _wait(page, check, what: str) -> None:
    """Poll `check` (server state, or the page): the answer to a key press
    lands a request and a swap later, and `settle` cannot wait for either."""
    for _ in range(120):
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(what)


def _says(page, button: str, title: str) -> None:
    """The topbar's undo / redo button reads `title` (its redraw landed)."""
    _wait(
        page,
        lambda: page.get_attribute(button, "title") == title,
        f"{button} never said {title!r}: {page.get_attribute(button, 'title')!r}",
    )


def _focus(page) -> str:
    return page.evaluate(
        "() => { const a = document.activeElement;"
        " return a.id || a.className || a.tagName; }"
    )


def _cursor(page) -> str | None:
    return page.evaluate("() => document.querySelector('.arr-cell.cursor')?.id ?? null")


def test_ctrl_z_and_ctrl_shift_z_walk_the_songs_edits(studio, server):
    """Edit, Ctrl+Z, Ctrl+Shift+Z, Ctrl+Y on the Song page: the song's own
    ring, driven from the keyboard with the arranger focused. The arranger
    is replaced by each answer, so what is checked is what a user keeps:
    the cell, the grid's keyboard focus, the cursor's place, and buttons
    that say what is next."""
    page = _song_with_a_section(studio, server)
    sid, drums, synth = _cell_ids(server)
    full = _cells(server)
    # "Add loop to song" is a step already; there is nothing to redo
    assert page.get_attribute("#song-undo", "aria-disabled") == "false"
    assert (
        page.get_attribute("#song-undo", "title") == "Undo: add loop to song (Ctrl+Z)"
    )
    assert page.get_attribute("#song-redo", "aria-disabled") == "true"

    page.locator(f"#cell-{sid}-{synth} .cell-btn").click()  # cursor + grid focus
    page.wait_for_selector("#song-drawer:not([hidden])")
    studio.settle()
    page.keyboard.press("Delete")
    _wait(page, lambda: _cells(server).get(synth) is None, "Delete cleared no cell")
    _says(page, "#song-undo", "Undo: clear cell (Ctrl+Z)")
    page.wait_for_selector(f"#cell-{sid}-{synth}:not(.filled)")

    page.keyboard.press("Control+z")
    _wait(page, lambda: _cells(server) == full, "Ctrl+Z did not bring the cell back")
    _says(page, "#song-redo", "Redo: clear cell (Ctrl+Shift+Z)")
    _says(page, "#song-undo", "Undo: add loop to song (Ctrl+Z)")
    page.wait_for_selector(f"#cell-{sid}-{synth}.filled")
    assert _focus(page) == "arranger-grid"  # the keyboard is still on the grid
    assert _cursor(page) == f"cell-{sid}-{synth}"  # and the cursor where it was
    # the open drawer shows the restored pattern
    page.wait_for_selector(f'#song-drawer[data-pattern-id="{full[synth]}"]')

    page.keyboard.press("Control+Shift+Z")
    _wait(page, lambda: _cells(server).get(synth) is None, "Ctrl+Shift+Z did not redo")
    _says(page, "#song-redo", "Nothing to redo")
    page.wait_for_selector(f"#cell-{sid}-{synth}:not(.filled)")
    assert _focus(page) == "arranger-grid" and _cursor(page) == f"cell-{sid}-{synth}"
    page.keyboard.press("Control+z")
    _wait(page, lambda: _cells(server) == full, "the second Ctrl+Z did nothing")
    _says(page, "#song-redo", "Redo: clear cell (Ctrl+Shift+Z)")
    page.keyboard.press("Control+y")
    _wait(page, lambda: _cells(server).get(synth) is None, "Ctrl+Y did not redo")
    _says(page, "#song-redo", "Nothing to redo")
    page.keyboard.press("Control+z")
    _wait(page, lambda: _cells(server) == full, "Ctrl+Z after Ctrl+Y did nothing")
    _says(page, "#song-redo", "Redo: clear cell (Ctrl+Shift+Z)")
    page.wait_for_selector("#song-drawer .drawer-rename input")

    # typing in a field: Ctrl+Z is the field's own undo, never the song's
    name = page.locator("#song-drawer .drawer-rename input")
    name.fill("typed")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(400)
    assert _cells(server) == full
    assert (
        page.get_attribute("#song-undo", "title") == "Undo: add loop to song (Ctrl+Z)"
    )
    assert (
        page.get_attribute("#song-redo", "title") == "Redo: clear cell (Ctrl+Shift+Z)"
    )

    # the buttons from the keyboard: the topbar morphs, so the button
    # pressed is still the one with focus
    page.focus("#song-redo")
    page.keyboard.press("Enter")
    _wait(
        page, lambda: _cells(server).get(synth) is None, "the redo button did nothing"
    )
    _says(page, "#song-redo", "Nothing to redo")
    assert _focus(page) == "song-redo"
    assert page.get_attribute("#song-redo", "aria-disabled") == "true"
    page.keyboard.press("Enter")  # nothing to redo: no post, no error, no change
    page.wait_for_timeout(400)
    assert _cells(server).get(synth) is None and _focus(page) == "song-redo"
    page.focus("#song-undo")
    page.keyboard.press("Enter")
    _wait(page, lambda: _cells(server) == full, "the undo button did nothing")
    _says(page, "#song-redo", "Redo: clear cell (Ctrl+Shift+Z)")
    assert _focus(page) == "song-undo"
    studio.settle()


def test_clear_pattern_asks_nothing_and_ctrl_z_brings_the_notes_back(studio, server):
    page = _song_with_a_section(studio, server)
    sid, _drums, synth = _cell_ids(server)
    pid = _cells(server)[synth]
    notes = [list(c) for c in server.state.song["patterns"][pid]["cells"]]
    assert notes
    page.locator(f"#cell-{sid}-{synth} .cell-btn").click()
    page.wait_for_selector('#song-drawer:not([hidden])[data-kind="notes"]')
    studio.settle()
    lit = _drawer_steps(page)
    assert lit

    page.click('#song-drawer [aria-label="clear the pattern"]')  # (no question asked)
    _wait(
        page,
        lambda: server.state.song["patterns"][pid]["cells"] == [],
        "the pattern was not cleared (a confirm dialog in the way?)",
    )
    _wait(page, lambda: _drawer_steps(page) == [], "the drawer still shows the notes")
    _says(page, "#song-undo", "Undo: clear pattern (Ctrl+Z)")
    page.keyboard.press("Control+z")
    _wait(
        page,
        lambda: server.state.song["patterns"][pid]["cells"] == notes,
        "Ctrl+Z did not bring the notes back",
    )
    # the open drawer shows them again
    _wait(page, lambda: _drawer_steps(page) == lit, "the drawer did not redraw them")
    studio.settle()


def test_solo_this_cell_sounds_alone_and_stops_with_the_drawer(studio, server):
    """The drawer's toggle loops the cell's track on its own: signal on the
    master bus, the song's transport not running, and silence again when
    the drawer closes. Proves the audition reaches the output and stops;
    nobody hears that it is the right track (tests/studio/test_song_solo.py
    pins the audio)."""
    page = _song_with_a_section(studio, server)
    sid, _drums, synth = _cell_ids(server)
    page.locator(f"#cell-{sid}-{synth} .cell-btn").click()
    page.wait_for_selector("#song-drawer .drawer-solo")
    studio.settle()
    solo = page.locator("#song-drawer .drawer-solo")
    assert solo.get_attribute("aria-pressed") == "false"

    solo.focus()
    page.keyboard.press("Space")  # a button: the keyboard presses it
    page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    studio.wait_sound()
    assert page.evaluate("() => window.synthlab.songClock().playing") is False
    assert page.get_attribute("#song-play", "aria-pressed") == "false"

    # an edit while it loops: the drawer morphs, the audition carries on
    page.click('#song-drawer [aria-label="transpose up an octave"]')
    studio.settle()
    page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    studio.wait_sound()

    solo.focus()
    page.keyboard.press("Space")  # the second press stops it
    page.wait_for_selector('.drawer-solo[aria-pressed="false"]')
    studio.wait_silence()

    solo.click()
    page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    studio.wait_sound()
    page.click("#song-drawer .drawer-close")  # closing the drawer stops it
    page.wait_for_selector("#song-drawer[hidden]", state="attached")
    studio.wait_silence()
    studio.settle()


def test_enter_after_delete_opens_the_cell_under_the_cursor(studio, server):
    """Click the drums cell, ArrowDown, Delete, Enter: the drawer must open
    on the cell the cursor is on (the synth cell, now empty), not on the
    drums cell the pointer last clicked. Found by the browser pass: the
    redraw after Delete left the cursor's mark on no cell at all for the
    next key."""
    page = _song_with_a_section(studio, server)
    sid, drums, synth = _cell_ids(server)
    page.locator(f"#cell-{sid}-{drums} .cell-btn").click()
    page.wait_for_selector('#song-drawer:not([hidden])[data-kind="drums"]')
    studio.settle()
    page.keyboard.press("ArrowDown")
    assert _cursor(page) == f"cell-{sid}-{synth}"
    page.keyboard.press("Delete")
    _wait(page, lambda: _cells(server).get(synth) is None, "Delete cleared no cell")
    # the redrawn arranger is in the page, and htmx's settle (which is what
    # used to drop the mark) has run
    page.wait_for_selector(f"#cell-{sid}-{synth}:not(.filled)")
    page.wait_for_timeout(300)
    assert _cells(server).get(drums) is not None  # the drums cell is untouched
    assert _cursor(page) == f"cell-{sid}-{synth}"

    page.keyboard.press("Enter")
    page.wait_for_selector(f'#song-drawer:not([hidden])[data-track-id="{synth}"]')
    assert server.state.song_drawer == {"section_id": sid, "track_id": synth}
    assert page.locator("#song-drawer .drawer-empty").count() == 1
    assert _focus(page) == "arranger-grid"
    studio.settle()


# --- an FX-parameter lane ------------------------------------------------------------


def _export(studio) -> bytes:
    r = studio.page.request.get(f"{studio.url}/export/song.wav")
    assert r.status == 200
    return r.body()


def test_an_fx_lane_is_added_from_the_drawer_persists_and_is_heard_in_the_export(
    studio, server
):
    """The loop's layer carries a reverb; in the song the drawer's "+ Lane"
    menu offers its mix under the unit's name. Adding it names the unit
    (an id), shaping it changes the exported song, and both are still there
    after a reload. Proves the lane reaches the render; nobody hears it."""
    from synthlab.studio.fxrack import clean_chain

    state = server.state
    state.layers[state.active_layer]["fx"] = clean_chain(
        [{"kind": "reverb", "params": {"decay": 1.5, "mix": 0.2}}]
    )
    _with_a_beat(server)
    page = studio.open("song")
    studio.settle()
    page.click('button[hx-post="/song/from-loop"]')
    page.wait_for_selector(".arr-cell.filled")
    studio.settle()
    sid, _drums, synth = _cell_ids(server)

    def track() -> dict:  # read fresh: an edit replaces the song
        return next(t for t in server.state.song["tracks"] if t["id"] == synth)

    assert "id" not in track()["fx"][0]
    page.locator(f"#cell-{sid}-{synth} .cell-btn").click()
    page.wait_for_selector("#song-drawer .lane-add")
    studio.settle()
    before = _export(studio)

    group = page.locator('.lane-add optgroup[label="Reverb"] option')
    assert group.all_inner_texts() == ["Reverb mix"]
    page.select_option(".lane-add select", "fx:@0:reverb:mix")
    page.click(".lane-add button[type=submit]")
    row = '.lane-row[data-target^="fx:"]'
    page.wait_for_selector(row)
    studio.settle()
    uid = track()["fx"][0]["id"]
    target = f"fx:{uid}:reverb:mix"
    assert _lanes(server)[synth] == [
        {"target": target, "points": [[0.0, 0.2], [1.0, 0.2]]}
    ]
    assert page.inner_text(f"{row} .lane-name") == "Reverb mix"
    assert page.locator(f"{row} output").first.inner_text() == "20%"
    assert page.locator(".arr-cell .cell-auto").count() == 1

    page.locator(f'{row} input[name="end"]').fill("1")
    _wait(
        page,
        lambda: _lanes(server)[synth][0]["points"] == [[0.0, 0.2], [1.0, 1.0]],
        "the slider never reached the lane",
    )
    studio.settle()
    assert page.locator(f"{row} output").last.inner_text() == "100%"
    after = _export(studio)
    assert len(after) == len(before) and after != before  # the room comes up

    page.reload()
    page.wait_for_selector(row)
    assert page.get_attribute(row, "data-target") == target
    assert track()["fx"][0]["id"] == uid and _export(studio) == after
    # Ctrl+Z takes the shape back, then the lane and the unit's id together
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(
        page,
        lambda: _lanes(server)[synth][0]["points"][-1] == [1.0, 0.2],
        "Ctrl+Z did not undo the slider",
    )
    page.keyboard.press("Control+z")
    _wait(page, lambda: not _lanes(server), "Ctrl+Z did not remove the lane")
    assert "id" not in track()["fx"][0]
    studio.settle()


# --- a cell dragged onto another -----------------------------------------------------


def _two_sections(studio, server):
    """A loop as section A, and an empty section B after it."""
    page = _song_with_a_section(studio, server)
    page.click('[hx-post="/song/section/add"]')
    _wait(page, lambda: len(server.state.song["sections"]) == 2, "no second section")
    studio.settle()
    a, b = (sec["id"] for sec in server.state.song["sections"])
    page.wait_for_selector(f'.arr-cell[data-section-id="{b}"]')  # the redraw landed
    return page, a, b


def _all_cells(server) -> list[dict]:
    return [dict(sec["cells"]) for sec in server.state.song["sections"]]


def test_a_cell_drags_onto_another_ctrl_copies_it_and_ctrl_z_is_one_step(
    studio, server
):
    """Drag the drums cell of A onto B's: B plays it and A's is empty — a
    MOVE, one edit, so one Ctrl+Z puts it back. Ctrl-drag the synth cell:
    both play the pattern (shared). The rule was the other way round for
    its first week (plain = copy, Alt / Shift = move); the playlist flipped
    it (docs/song.md, "Drags onto a lane"). A click is still a click (it
    opens the drawer), and a drop on the other kind of track is refused
    with a toast."""
    page, a, b = _two_sections(studio, server)
    _sid, drums, synth = _cell_ids(server)
    full = _cells(server)

    def cell(sec: str, tid: str):
        return page.locator(f"#cell-{sec}-{tid}")

    # a click is not a drag
    cell(a, synth).locator(".cell-btn").click()
    page.wait_for_selector('#song-drawer:not([hidden])[data-kind="notes"]')
    studio.settle()
    assert _all_cells(server)[0] == full
    assert not any(_all_cells(server)[1].values())

    # Ctrl-drag: a copy by reference
    page.keyboard.down("Control")
    cell(a, synth).drag_to(cell(b, synth))
    page.keyboard.up("Control")
    _wait(
        page,
        lambda: _all_cells(server)[1].get(synth) == full[synth],
        "the Ctrl-drop copied nothing",
    )
    assert _all_cells(server)[0] == full  # the source still plays it
    page.wait_for_selector(f"#cell-{b}-{synth}.filled")
    studio.settle()
    assert "×2" in cell(a, synth).inner_text()  # shared, and it says so
    _says(page, "#song-undo", "Undo: set cell (Ctrl+Z)")

    # the other kind of track: a toast, nothing changes
    cell(a, synth).drag_to(cell(b, drums))
    page.wait_for_selector(".toast")
    assert "can't go on this track" in page.inner_text(".toast")
    assert not _all_cells(server)[1].get(drums)

    # a plain drag: a move
    cell(a, drums).drag_to(cell(b, drums))
    _wait(
        page,
        lambda: _all_cells(server)[1].get(drums) == full[drums],
        "the drop moved nothing",
    )
    assert _all_cells(server)[0].get(drums) is None  # the source is cleared
    page.wait_for_selector(f"#cell-{a}-{drums}:not(.filled)")
    _says(page, "#song-undo", "Undo: move cell (Ctrl+Z)")
    studio.settle()

    # one Ctrl+Z: the pattern is back where it was, and gone from B
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")
    _wait(
        page,
        lambda: _all_cells(server)[0] == full and not _all_cells(server)[1].get(drums),
        "one Ctrl+Z did not take the move back",
    )
    _says(page, "#song-undo", "Undo: set cell (Ctrl+Z)")
    assert _all_cells(server)[1].get(synth) == full[synth]  # the copy: another step
    studio.settle()


_TOUCH_DRAG = """([from, to]) => {
  const src = document.getElementById(from), dst = document.getElementById(to);
  const dt = new DataTransfer();
  const fire = (el, type) => el.dispatchEvent(
    new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
  src.dispatchEvent(
    new PointerEvent("pointerdown", { bubbles: true, pointerType: "touch" }));
  fire(src, "dragstart"); fire(dst, "dragover");
  const marked = dst.classList.contains("drop-ok");
  fire(dst, "drop"); fire(src, "dragend");
  return marked;
}"""


def test_a_touch_drop_asks_move_or_copy(studio, server):
    """No modifier under a finger: the drop asks, Move first (it is what a
    drag does now). Driven with synthetic events (Playwright cannot start a
    native touch drag), so this proves the island's own handling, not the
    browser's touch drag."""
    page, a, b = _two_sections(studio, server)
    _sid, drums, _synth = _cell_ids(server)
    full = _cells(server)
    ids = [f"cell-{a}-{drums}", f"cell-{b}-{drums}"]
    assert page.evaluate(_TOUCH_DRAG, ids) is True
    page.wait_for_selector(".cell-drop:popover-open")
    assert page.locator(".cell-drop button").all_inner_texts() == [
        "Move here",
        "Copy here",
    ]
    assert not _all_cells(server)[1].get(drums)  # nothing yet: it asked
    page.keyboard.press("Escape")  # neither
    page.wait_for_selector(".cell-drop", state="detached")
    assert _all_cells(server)[0] == full and not _all_cells(server)[1].get(drums)

    page.evaluate(_TOUCH_DRAG, ids)
    page.click('.cell-drop button:has-text("Move here")')
    _wait(
        page,
        lambda: (
            _all_cells(server)[1].get(drums) == full[drums]
            and _all_cells(server)[0].get(drums) is None
        ),
        "Move here moved nothing",
    )
    assert page.locator(".cell-drop").count() == 0
    studio.settle()


# --- a lane's sound, on the Song page -----------------------------------------


def test_a_lanes_sound_menu_fills_as_it_is_reached_for_and_swaps_the_sound(
    studio, server
):
    """The menu is drawn holding its sound's name alone (the list is on the
    page once, in #lane-sounds); a pointer coming down fills it, and a pick
    changes that lane's sound and nothing else of it."""
    page = _song_with_a_section(studio, server)
    track = next(t for t in server.state.song["tracks"] if t["kind"] == "synth")
    pick = page.locator(f"#trk-{track['id']} .track-sound-pick")
    assert pick.locator("option").count() == 1
    assert page.locator(f"#trk-{track['id']} .track-name").bounding_box()["width"] > 20
    pick.dispatch_event("pointerdown")
    assert pick.locator("optgroup").count() >= 2
    was = dict(track)
    option = pick.locator("optgroup >> nth=1").locator("option").nth(2)
    # (read now: the pick redraws the playlist, and the menu with it)
    value, name = option.get_attribute("value"), option.inner_text()
    with page.expect_response("**/song/track/*/sound"):
        pick.select_option(value)
    studio.settle()
    now = next(t for t in server.state.song["tracks"] if t["id"] == track["id"])
    assert now["patch"]["name"] == name != was["patch"]["name"]
    assert (now["gain"], now["mute"], now["name"]) == (
        was["gain"],
        was["mute"],
        was["name"],
    )
    shown = page.locator(f"#trk-{track['id']} .track-sound-pick option").first
    assert shown.inner_text() == now["patch"]["name"]


def test_the_sound_dock_holds_synths_for_a_lane_and_closes(studio, server):
    """✎ on a synth lane: Synths itself, in a frame across the bottom of the
    Song page, editing that track with no shell of its own; a change made in
    it is the track's; ✕ ends the edit and the track keeps the sound."""
    page = _song_with_a_section(studio, server)
    track = next(t for t in server.state.song["tracks"] if t["kind"] == "synth")
    page.click(f"#trk-{track['id']} .track-sound")
    page.wait_for_selector("#song-dock:not([hidden]) iframe")
    assert server.state.song_edit_track == track["id"]
    frame = page.frame_locator("#song-dock iframe")
    frame.locator("#track-edit-banner .teb-line").wait_for()
    assert frame.locator("html").get_attribute("data-embed") is not None
    assert not frame.locator(".topbar").is_visible()
    dock, view = page.locator("#song-dock").bounding_box(), page.viewport_size
    assert dock["width"] > 0.9 * view["width"] - 60  # the whole bar, under the rail
    assert page.evaluate(
        "() => document.documentElement.scrollHeight <= innerHeight + 1"
    )
    cutoff = frame.locator('input[name="cutoff"]').first
    was = float(cutoff.input_value())
    with page.expect_response("**/fragment/patch"):
        cutoff.evaluate(
            """(el) => { el.value = String(Number(el.value) * 0.6);
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true })); }"""
        )
    now = next(t for t in server.state.song["tracks"] if t["id"] == track["id"])
    assert now["patch"]["cutoff"] < was
    page.click(".sdock-close")
    page.wait_for_selector("#song-dock", state="hidden")
    assert server.state.song_edit_track is None
    kept = next(t for t in server.state.song["tracks"] if t["id"] == track["id"])
    assert kept["patch"]["cutoff"] == now["patch"]["cutoff"]


def test_the_dock_follows_a_block_and_holds_the_drum_lab_for_the_kit(studio, server):
    """With the dock open, a block that is opened moves it to that lane's
    instrument: a beat brings the Drum Lab, on the SONG's kit (its banner
    says so), and a kit control moved there is the drums track's."""
    page = _song_with_a_section(studio, server)
    tracks = server.state.song["tracks"]
    synth = next(t for t in tracks if t["kind"] == "synth")
    drums = next(t for t in tracks if t["kind"] == "drums")
    page.click(f"#trk-{synth['id']} .track-name")  # the name opens it here
    page.wait_for_selector("#song-dock:not([hidden]) iframe")
    assert server.state.song_edit_track == synth["id"]
    assert page.url.endswith("/view/song")
    page.locator(f"#trk-{drums['id']} .arr-cell.filled .cell-btn").first.click()
    page.wait_for_function(
        "() => document.querySelector('#song-dock iframe')"
        "?.getAttribute('src').includes('/view/drums')"
    )
    assert server.state.song_edit_track == drums["id"]
    frame = page.frame_locator("#song-dock iframe")
    frame.locator("#track-edit-banner .teb-line").wait_for()
    assert "kit of the song" in frame.locator("#track-edit-banner").inner_text()
    was = dict(drums.get("drum_mix") or {})
    level = frame.locator("#drum-grid-kick-level")
    with page.expect_response(lambda r: r.request.method == "POST"):
        level.evaluate(
            """(el) => { el.value = String(Number(el.max) * 0.5);
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true })); }"""
        )
    _wait(
        page,
        lambda: (
            next(t for t in server.state.song["tracks"] if t["id"] == drums["id"]).get(
                "drum_mix"
            )
            != was
        ),
        "the kit change reaching the song's drums track",
    )
    # the other choice is in the track's menu: the whole page
    page.click(f"#trk-{synth['id']} .sec-menu-btn")
    menu = page.locator(f"#trkmenu-{synth['id']}")
    assert menu.get_by_text("Edit sound here").count() == 1
    assert menu.get_by_text("Open on the Synths page").count() == 1
    page.keyboard.press("Escape")
    page.click(".sdock-close")
    page.wait_for_selector("#song-dock", state="hidden")
    assert server.state.song_edit_track is None
