"""From the Loops page to the Song in Chromium.

What these journeys prove: a launcher row's →§ makes a section and a
second press asks "already in the song" (Place it again makes another);
a loop row of the drawer dragged onto a synth lane lands from the bar under
the pointer, and onto a section's head makes a new section there; past the
8 synth tracks → Song asks which track takes it, and the choice lands it;
Scene → Song makes the scenes
launched, as they played, sections in order.
Ctrl+Z on Song takes each back. The song is the server's — nobody has
listened: whether a captured section sounds as the launcher did is not
checked here (test_to_song.py and test_jam.py hold the bars).
"""

from __future__ import annotations

import time

from synthlab.studio import loops
from synthlab.studio import song_model as sm
from synthlab.studio.state.model import Arrangement
from synthlab.synth import Patch

ASK = ".song-ask:popover-open"


def _wait(page, check, what: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _pay(sound: str, cutoff: float = 2000.0) -> dict:
    return {
        "format": 2,
        "patch": Patch(name=sound, cutoff=cutoff).to_json(),
        "layers": [],
        "main_fx": [],
        "curves": {},
    }


def _seed(server, bpm: float = 124.0) -> None:
    """Two loops of notes in two sounds and a beat, in scene 1 (Intro:
    the acid line and the beat) and scene 2 (Drop: both lines)."""
    d, s = server.data_dir, server.state
    for name, sound, cells in (
        ("Acid line", "Acid bass", [["A2", 0], ["C3", 4], ["E3", 8]]),
        ("Pluck hook", "Glass pluck", [["E3", 0], ["G3", 6]]),
    ):
        arr = Arrangement(bpm=bpm, bars=1, steps_per_bar=16, cells=cells)
        loops.save(d, name, arr, _pay(sound))
        time.sleep(0.01)
    loops.save_beat(d, "Techno beat", s.drum_bank[0], Arrangement(bpm=bpm))
    s.set_slot(0, 0, "Acid line")
    s.set_slot(s.DRUM_LANE, 0, "Techno beat")
    s.set_slot(0, 1, "Acid line")
    s.set_slot(1, 1, "Pluck hook")
    s.name_scene(0, "Intro")
    s.name_scene(1, "Drop")


def _sections(server) -> list[str]:
    return [x["name"] for x in server.state.song["sections"]]


def _ctrl_z_on_song(studio, server, n: int) -> None:
    page = studio.page
    if not page.locator('#view[data-view="song"]').count():
        studio.nav("song")
    page.locator(".song-view h1").click()
    page.keyboard.press("Control+z")
    _wait(page, lambda: len(server.state.song["sections"]) == n, f"{n} sections")


def test_a_scene_to_the_song_and_already_in_the_song(studio, server):
    _seed(server)
    page = studio.open("perform")
    page.wait_for_selector("#slots .sl-song")
    page.locator('#slots .sl-row[data-row="0"] .sl-song').click()
    _wait(page, lambda: _sections(server) == ["Intro"], "the scene's section")
    sec = server.state.song["sections"][0]
    assert sum(1 for p in sec["cells"].values() if p) == 2  # its line and its beat
    # again: asked, nothing changes until "Place it again"
    page.locator('#slots .sl-row[data-row="0"] .sl-song').click()
    page.wait_for_selector(ASK)
    assert "already in the song" in page.locator(ASK).inner_text()
    assert _sections(server) == ["Intro"]
    page.locator(f"{ASK} button", has_text="Place it again").click()
    _wait(page, lambda: _sections(server) == ["Intro", "Intro"], "placed again")
    assert page.locator(ASK).count() == 0
    _ctrl_z_on_song(studio, server, 1)
    _ctrl_z_on_song(studio, server, 0)
    assert not studio.unexplained()


def _drag(page, src: tuple, dst: tuple) -> None:
    page.mouse.move(*src)
    page.mouse.down()
    page.mouse.move(src[0] + 6, src[1] + 4, steps=3)
    page.mouse.move(dst[0] - 4, dst[1], steps=6)
    page.mouse.move(*dst, steps=2)
    page.wait_for_timeout(80)
    page.mouse.up()


def _center(page, sel: str) -> tuple[float, float]:
    box = page.locator(sel).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_a_drawer_loop_dragged_onto_a_lane_and_onto_the_timeline(studio, server):
    _seed(server)
    s = server.state
    s.song = sm.new_song(124.0, "A1", "minor")
    tid = sm.add_track(s.song, "synth", "Keys", patch=Patch(name="Keys").to_json())
    pid = sm.add_pattern(
        s.song, "notes", "keys", {"bars": 1, "steps_per_bar": 16, "cells": [["C4", 0]]}
    )
    for name in ("Verse", "Chorus"):
        sid = sm.add_section(s.song, name, 4)
        sm.set_cell(s.song, sid, tid, pid)
    s.save()
    verse, chorus = (x["id"] for x in s.song["sections"])
    page = studio.open("song")
    studio.settle()
    page.click('[data-zoom-to="-1"]')
    page.click(".clips-btn")
    row = '#clip-drawer .lp-row[data-drag-loop][data-title="pluck hook"]'
    page.wait_for_selector(row)
    assert page.get_attribute(row, "draggable") == "true"
    # onto the Chorus's synth lane, its third bar: the pattern from there
    cell = page.locator(f"#cell-{chorus}-{tid}").bounding_box()
    bar_w = cell["width"] / 4
    dst = (cell["x"] + 2.5 * bar_w, cell["y"] + cell["height"] / 2)
    with page.expect_response("**/loop/song"):
        _drag(page, _center(page, f"{row} .lp-name"), dst)
    _wait(
        page,
        lambda: sm.cell_span(s.song["sections"][1], tid) == (2, 2),
        "the lane drop",
    )
    pat = s.song["patterns"][s.song["sections"][1]["cells"][tid]]
    assert pat["cells"] == [["E3", 0], ["G3", 6]]
    # onto the Verse's head, its left half: a new section before it
    head = page.locator(f"#sec-{verse}").bounding_box()
    dst = (head["x"] + 6, head["y"] + head["height"] / 2)
    if not page.locator("#clip-drawer:popover-open").count():
        page.click(".clips-btn")
    with page.expect_response("**/loop/song"):
        _drag(page, _center(page, f"{row} .lp-name"), dst)
    _wait(page, lambda: _sections(server)[0] == "Pluck hook", "the timeline drop")
    assert _sections(server) == ["Pluck hook", "Verse", "Chorus"]
    _ctrl_z_on_song(studio, server, 2)
    page.keyboard.press("Control+z")
    _wait(page, lambda: sm.cell_span(s.song["sections"][1], tid) == (0, 4), "undone")
    assert not studio.unexplained()


def test_the_track_cap_asks_which_track_takes_it(studio, server):
    _seed(server)
    s = server.state
    s.song = sm.new_song(124.0, "A1", "minor")
    sid = sm.add_section(s.song, "Full", 1)
    ids = []
    for i in range(8):
        tid = sm.add_track(
            s.song, "synth", f"Synth {i + 1}", patch=Patch(name=f"S{i}").to_json()
        )
        p = sm.add_pattern(
            s.song,
            "notes",
            f"p{i}",
            {"bars": 1, "steps_per_bar": 16, "cells": [["C4", i]]},
        )
        sm.set_cell(s.song, sid, tid, p)
        ids.append(tid)
    s.save()
    page = studio.open("perform")
    page.wait_for_selector("#slots .sl-song")
    page.locator('#slots .sl-row[data-row="1"] .sl-song').click()
    page.wait_for_selector(ASK)
    box = page.locator(ASK)
    assert "synth tracks are all in use" in box.inner_text()
    assert box.locator("a", has_text="Free a track").count() == 1
    box.locator("button", has_text="Synth 3: keeps S2").click()
    # the scene's second line asks again (its first went on Synth 3)
    page.wait_for_selector(ASK)
    page.locator(f"{ASK} button", has_text="Synth 5: becomes Glass pluck").click()
    _wait(page, lambda: len(s.song["sections"]) == 2, "the scene placed")
    new = s.song["sections"][1]["cells"]
    assert new[ids[2]] and new[ids[4]]
    assert (
        s.song["tracks"][sm.track_index(s.song, ids[4])]["patch"]["name"]
        == "Glass pluck"
    )
    _ctrl_z_on_song(studio, server, 1)
    assert s.song["tracks"][sm.track_index(s.song, ids[4])]["patch"]["name"] == "S4"
    assert not studio.unexplained()


def test_capture_scenes_makes_the_launches_sections_in_order(studio, server):
    _seed(server, bpm=240.0)  # a bar is a second
    s = server.state
    from dataclasses import replace

    s.arrangement = replace(s.arrangement, bpm=240.0)
    s.save()
    page = studio.open("perform")
    page.wait_for_selector("#slots .sl-scene")
    cap = page.locator("#perform-slots .sl-capture")
    assert cap.is_disabled() and cap.inner_text().startswith("Scene → Song")
    page.locator('#slots .sl-row[data-row="0"] .sl-scene').click()
    _wait(page, lambda: cap.get_attribute("data-n") == "1", "the first launch logged")
    page.wait_for_timeout(2300)
    page.locator('#slots .sl-row[data-row="1"] .sl-scene').click()
    _wait(page, lambda: cap.get_attribute("data-n") == "2", "the second launch logged")
    page.wait_for_timeout(1200)
    with page.expect_response("**/fragment/perform/scene/capture"):
        cap.click()
    _wait(page, lambda: len(s.song["sections"]) >= 2, "the captured sections")
    names = _sections(server)
    assert names[0] == "Intro" and names[-1] == "Drop", names
    # the Intro left to loop (a 1-bar scene for ~2-3 bars): its section repeated
    assert s.song["sections"][0]["bars"] == 1 and s.song["sections"][0]["repeats"] >= 2
    _wait(page, lambda: cap.is_disabled(), "the log dropped")
    _ctrl_z_on_song(studio, server, 0)
    assert not studio.unexplained()
