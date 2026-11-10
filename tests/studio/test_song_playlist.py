"""The Song page's playlist: the arranger drawn on a
true time axis — a bar ruler, every section as wide as bars x repeats,
blocks and their echoes, takes at their clip's length — and what the
islands read off it. Layout in pixels is the browser suite's
(tests/browser/test_song_playlist.py); this is the fragment.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import clips, limits, schema
from synthlab.studio.server import create_app
from synthlab.studio.service import REGIONS
from synthlab.studio.service.songpage import blocks, ruler, take_bars
from tests.studio.test_song_routes import post, song_dict, use_song

SR = 44100
STATIC = Path("synthlab/studio/static")
CSS = STATIC / "style.css"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def _arranger(client) -> str:
    html = client.get("/view/song").text
    start = html.rindex("<section", 0, html.index('id="song-arranger"'))
    return html[start : html.index("</section>", html.index("arr-foot", start))]


def _clean(song: dict) -> dict:
    return schema.conform(song, schema.nodes().SONG)


def _tag(html: str, marker: str) -> str:
    """The opening tag that holds `marker`."""
    at = html.index(marker)
    return html[html.rindex("<", 0, at) : html.index(">", at) + 1]


def _cell(html: str, sid: str, tid: str) -> str:
    start = html.index(f'id="cell-{sid}-{tid}"')
    return html[html.rindex("<td", 0, start) : html.index("</td>", start)]


def _wav(seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"\x00\x00" * int(seconds * SR))
    return buf.getvalue()


def _clip(client, name: str, seconds: float) -> None:
    d = clips.clip_dir(client.app.state.studio.data_dir)
    (d / f"{name}.wav").write_bytes(_wav(seconds))


def _long_song(bars: int, repeats: int = 1, sections: int = 1) -> dict:
    song = song_dict()
    song["sections"] = [
        {
            "id": f"s{i}",
            "name": f"S{i}",
            "bars": bars,
            "repeats": repeats,
            "cells": {"td": "pd", "t1": "p1"},
        }
        for i in range(sections)
    ]
    return song


# -- the time axis ------------------------------------------------------------------


def test_every_section_carries_its_bars_and_repeats_as_its_width(client):
    """A column is bars x repeats x --bar-w: the <col>, the head and every
    cell of a section carry the two numbers the CSS multiplies."""
    html = _arranger(client)
    assert '<col class="col-sec" id="col-s1" style="--bars: 2; --reps: 1">' in html
    assert '<col class="col-sec" id="col-s2" style="--bars: 2; --reps: 2">' in html
    assert "--bars: 2; --reps: 2" in _tag(html, 'id="sec-s2"')
    for tid in ("td", "t1", "t2", "ta"):
        assert "--bars: 2; --reps: 2" in _tag(html, f'id="cell-s2-{tid}"'), tid
    # the table is as wide as the song: the head, every bar, the add column
    assert '<table class="arranger-grid" style="--song-bars: 6">' in html
    css = CSS.read_text(encoding="utf-8")
    assert (
        ".col-sec { width: calc(var(--bars, 4) * var(--reps, 1) * var(--bar-w)); }"
        in css
    )
    assert re.search(
        r"\.arranger-grid \{ table-layout: fixed;[^}]*border-spacing: 0 ", css
    )
    assert re.search(
        r"\.song-view \{[^}]*--bar-w: 24px;", css
    )  # right before any script


def test_the_ruler_numbers_every_fourth_bar_of_a_64_bar_song(client):
    use_song(client, _long_song(16, repeats=2, sections=2))
    html = _arranger(client)
    assert 'id="song-ruler" class="song-ruler" data-bars="64"' in html
    numbers = [int(n) for n in re.findall(r'class="ruler-n" style="--b: (\d+)"', html)]
    assert numbers == list(range(1, 65, 4))
    assert numbers[0] == 1 and numbers[-1] == 61
    # the ruler spans the sections' columns, and only them
    assert '<th class="ruler-cell" colspan="2">' in html


def test_the_ruler_doubles_its_step_until_256_numbers_are_enough():
    song = _clean(_long_song(64, repeats=16, sections=4))  # 4096 bars: over the cap
    r = ruler(song)
    assert r["bars"] == 4096 and r["step"] == 16
    assert len(r["numbers"]) == 256 <= limits.MAX_RULER_NUMBERS
    assert r["numbers"][:3] == [1, 17, 33]
    assert ruler(_clean(_long_song(4)))["step"] == 4


def test_the_cap_mark_sits_on_the_bar_line_after_bar_bpm(client):
    """The cap (480 s) is 2 x `bpm` bars exactly (a bar is 240 / bpm
    seconds). Drawn when it falls inside the ruler or within 16 bars of
    its end."""
    assert limits.MAX_SONG_SECONDS == 480.0
    song = _long_song(16, repeats=16, sections=1)  # 256 bars at 120 bpm
    use_song(client, song)
    html = _arranger(client)
    assert '<i class="ruler-cap" style="--b: 240.0"' in html
    song["bpm"] = 93.002  # a stem's tempo
    assert ruler(_clean(song))["cap"] == 186.004
    near, far = _long_song(16, repeats=14), _long_song(16, repeats=13)
    assert ruler(_clean(near))["cap"] == 240.0  # 224 bars: 16 from the cap
    assert ruler(_clean(far))["cap"] is None  # 208 bars: 32 away
    use_song(client)
    assert "ruler-cap" not in _arranger(client)


def test_an_empty_song_draws_no_ruler(client):
    song = song_dict()
    song["sections"] = []
    use_song(client, song)
    html = _arranger(client)
    assert "song-ruler" not in html and "ruler-cell" not in html
    assert "No sections yet" in html


# -- blocks and echoes --------------------------------------------------------------


def test_a_block_fills_its_section_and_each_later_pass_is_an_echo(client):
    html = _arranger(client)
    once = _cell(html, "s1", "td")  # Intro: 2 bars, once
    assert "--at: 0; --len: 2" in _tag(once, 'id="cell-s1-td"')
    assert 'class="cell-btn blk"' in once and 'class="echo"' not in once
    twice = _cell(html, "s2", "td")  # Verse: 2 bars, twice
    assert twice.count('<i class="echo"') == 1
    echo = twice[twice.index('<i class="echo"') :]
    assert 'style="--pass: 1"' in echo and 'aria-hidden="true"' in echo
    # the echo is the block's tiles again, drawn once and <use>d: no text
    assert '<g id="tiles-s2-td">' in twice and '<use href="#tiles-s2-td"/>' in echo
    assert "cell-name" not in echo
    # an empty cell offers + and has no block, no echo
    empty = _cell(html, "s1", "t2")
    assert "cell-add" in empty and "blk" not in empty and "echo" not in empty


def test_a_section_head_marks_each_repeat(client):
    song = song_dict()
    song["sections"][1]["repeats"] = 3
    use_song(client, song)
    html = _arranger(client)
    head = html[
        html.index('id="sec-s2"') : html.index("</th>", html.index('id="sec-s2"'))
    ]
    assert re.findall(r'class="sec-pass" style="--pass: (\d)"', head) == ["1", "2"]
    assert 'data-start-bar="3"' in _tag(html, 'id="sec-s2"')
    assert 'title="Verse · bars 3–8"' in _tag(html, 'id="sec-s2"')
    one = html[
        html.index('id="sec-s1"') : html.index("</th>", html.index('id="sec-s1"'))
    ]
    assert "sec-pass" not in one
    assert "sec-start" not in html  # the corner bar number: the ruler has it


def test_the_fragment_stops_drawing_thumbnails_at_the_tile_bound():
    """A legal song is at most 400 bars a lane; an over-cap import can be
    far longer, so the fragment counts tiles and the blocks past the bound
    are plain colour."""
    raw = _long_song(64, repeats=16, sections=32)
    for sec in raw["sections"]:  # a third lane: 3 x 32 x (64 + 15) tiles
        sec["cells"]["t2"] = "p1"
    song = _clean(raw)
    got = blocks(song)
    drawn = sum(b["tiles"] + 15 for b in got.values() if b["tiles"])
    assert 0 < drawn <= limits.MAX_PLAYLIST_TILES
    assert any(b["tiles"] == 0 for b in got.values())
    assert all(b["len"] == 64 and b["at"] == 0 for b in got.values())
    small = blocks(_clean(song_dict()))
    assert small[("s1", "td")] == {"at": 0, "len": 2, "tiles": 2}
    assert small[("s2", "t2")] == {"at": 0, "len": 2, "tiles": 1}  # a 2-bar pattern


def test_an_over_cap_song_still_renders_its_fragment(client):
    use_song(client, _long_song(64, repeats=16, sections=32))
    html = _arranger(client)
    assert html.count("<use ") <= limits.MAX_PLAYLIST_TILES
    assert html.count('class="cell-btn blk"') == 64  # every block is there


# -- takes --------------------------------------------------------------------------


def _with_take(client, seconds: float, at_bar: int = 0, sid: str = "s1") -> None:
    _clip(client, "hit", seconds)
    song = song_dict()
    sec = next(s for s in song["sections"] if s["id"] == sid)
    sec["audio"] = [{"clip": "hit", "at_bar": at_bar, "track_id": "ta"}]
    use_song(client, song)


def test_a_take_is_as_wide_as_its_clip_is_long(client):
    """seconds x bpm / 240 bars: one second at 120 bpm is half a bar."""
    _with_take(client, 1.0)
    s = client.app.state.studio
    assert take_bars(s.data_dir, s.song) == {"hit": 0.5}
    cell = _cell(_arranger(client), "s1", "ta")
    chip = _tag(cell, 'data-clip="hit"')
    assert 'class="take-chip"' in chip and 'data-at-bar="0"' in chip
    assert 'data-bars="0.5"' in chip and "--at: 0; --len: 0.5" in chip


def test_a_take_longer_than_its_room_is_cut_at_the_sections_end(client):
    _with_take(client, 6.0, at_bar=1)  # 3 bars, from bar 2 of a 2-bar section
    chip = _tag(_cell(_arranger(client), "s1", "ta"), 'data-clip="hit"')
    assert 'class="take-chip take-cut"' in chip
    assert 'data-bars="3.0"' in chip and "--at: 1; --len: 1" in chip


def test_a_take_is_echoed_in_each_later_pass(client):
    _with_take(client, 2.0, at_bar=1, sid="s2")
    cell = _cell(_arranger(client), "s2", "ta")
    assert cell.count("take-echo") == 1
    assert 'style="--at: 1; --len: 1.0; --pass: 1"' in cell


def test_a_take_with_no_clip_on_disk_still_draws(client):
    song = song_dict()
    song["sections"][0]["audio"] = [{"clip": "gone", "at_bar": 0, "track_id": "ta"}]
    use_song(client, song)
    chip = _tag(_cell(_arranger(client), "s1", "ta"), 'data-clip="gone"')
    assert 'data-bars="0.0"' in chip and "--len: 0.25" in chip


# -- what the islands select by ------------------------------------------------------


def test_the_ids_and_data_the_islands_read_are_all_still_there(client):
    """The playlist replaced the grid in place: same region, same table,
    same ids and data-*, so song.js, songcell.js, songrec.js and
    songdrag.js keep working. This is the list they select by."""
    _with_take(client, 1.0)
    html = _arranger(client)
    assert 'id="arr-scroll"' in html and 'class="arranger-grid"' in html
    assert "data-sections=" in _tag(html, 'id="song-arranger"')
    assert "data-song-bars=" in _tag(html, 'id="song-arranger"')
    for sid in ("s1", "s2"):
        head = _tag(html, f'id="sec-{sid}"')
        assert 'class="arr-section"' in head  # song.js, songrec.js
        for name in ("data-section-id", "data-index", "data-bars", "data-repeats"):
            assert f"{name}=" in head, name
        assert f'data-resize="{sid}"' in html  # the section's edge
    for tid, kind in (("td", "drums"), ("t1", "synth"), ("ta", "audio")):
        row = _tag(html, f'id="trk-{tid}"')
        assert f'data-track-id="{tid}"' in row and f'data-kind="{kind}"' in row
        assert f'data-arm="{tid}"' in html  # songrec.js
    filled = _tag(html, 'id="cell-s1-td"')
    assert 'class="arr-cell filled"' in filled and 'draggable="true"' in filled
    assert 'data-pattern-id="pd"' in filled and 'data-kind="drums"' in filled
    assert 'data-section-id="s1"' in filled and 'data-track-id="td"' in filled
    empty = _tag(html, 'id="cell-s1-t2"')
    assert 'data-pattern-id=""' in empty and 'data-kind="notes"' in empty
    audio = _tag(html, 'id="cell-s1-ta"')
    assert "arr-takes" in audio and "data-kind" not in audio
    # the block is the button that opens the drawer, and the rows are in
    # <tbody> (song.js walks `.arranger-grid tbody tr`)
    body = html[html.index("<tbody>") : html.index("</tbody>")]
    assert body.count("<tr ") == 4 and 'hx-post="/song/drawer"' in body
    assert "arr-page" not in html and "scroll-button" not in html


def test_the_paging_is_gone_from_the_island_and_the_css():
    js = (STATIC / "song.js").read_text(encoding="utf-8")
    assert "pager" not in js and "scroll-button" not in js
    css = CSS.read_text(encoding="utf-8")
    assert "scroll-snap" not in css.split("9b5. Song view")[1].split("9c. View swap")[0]
    assert "::scroll-button" not in css and ".arr-page" not in css


def test_the_take_menu_is_a_popover_not_a_panel_the_scroller_clips(client):
    _with_take(client, 1.0)
    cell = _cell(_arranger(client), "s1", "ta")
    assert 'popovertarget="takemenu-s1-ta-0"' in cell
    assert 'id="takemenu-s1-ta-0" class="pop menu-pop take-menu" popover' in cell
    assert 'hx-post="/song/take/hit/rename"' in cell
    assert 'hx-post="/fragment/sampler/clip"' in cell


# -- songline.js's pure half, under node ---------------------------------------------

HARNESS = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: songline.js is a classic script (an IIFE merging onto
// window.synthlab). With no `document` it publishes songLine and stops.
eval(fs.readFileSync(process.argv[2], "utf8"));
const L = window.synthlab.songLine;
const calls = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(calls.map(([op, ...args]) => L[op](...args))));
"""

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run songline.js's own code"
)


def line(tmp_path, *calls):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    spec = tmp_path / "calls.json"
    spec.write_text(json.dumps(calls), encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(STATIC / "songline.js"), str(spec)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(out.stdout)


@node
def test_bar_at_reads_the_bar_under_a_pixel(tmp_path):
    got = line(
        tmp_path,
        ["barAt", 0, 24],
        ["barAt", 23.9, 24],
        ["barAt", 24, 24],
        ["barAt", 100, 8],
        ["barAt", -5, 24],  # left of the ruler: its first bar
        ["barAt", 47.9, 24],
    )
    assert got == [0, 0, 1, 12, 0, 1]


@node
def test_zoom_steps_hold_at_both_ends(tmp_path):
    got = line(
        tmp_path,
        ["zoomStep", 24, 1],
        ["zoomStep", 24, -1],
        ["zoomStep", 96, 1],  # the top: stays
        ["zoomStep", 8, -1],  # the bottom: stays
        ["zoomStep", 25.5, 1],  # not a step (an old value): from the nearest
        ["zoomStep", 16, -1, True],  # a coarse pointer starts at 16
        ["zoomStep", 8, 1, True],
    )
    assert got == [32, 16, 96, 8, 32, 16, 24]
    (steps,) = line(tmp_path, ["fit", 0, 1])  # (a call, to read STEPS beside it)
    assert steps == 8


@node
def test_fit_shows_the_whole_song_in_the_lanes(tmp_path):
    """Fit fills the lanes to a quarter px, inside the steps' range (until
    Nov 2026 it was the largest step that fit, which left up to a fifth of
    a wide window empty)."""
    got = line(
        tmp_path,
        ["fit", 1200, 64],  # 18.75 px a bar, exactly
        ["fit", 1200, 21],  # 57.14 -> 57: never a bar off screen
        ["fit", 1200, 200],  # 6 -> 8, the smallest: a long song scrolls
        ["fit", 1200, 4],  # 300 -> 96, the largest
        ["fit", 700, 200, True],  # coarse: never under 16
        ["fit", 900, 0],  # an empty song divides by nothing
        ["fit", 1200, 50],  # exactly 24: it fits
    )
    assert got == [18.75, 57, 8, 96, 16, 96, 24]


@node
def test_passes_rounds_a_drag_to_whole_passes(tmp_path):
    got = line(
        tmp_path,
        ["passes", 96, 4, 24],  # one 4-bar pass
        ["passes", 47, 4, 24],  # not yet half: none
        ["passes", 49, 4, 24],  # past half: one
        ["passes", -200, 4, 24],
        ["passes", 290, 1, 24],
    )
    assert got == [1, 0, 1, -2, 12]


# -- the watch ----------------------------------------------------------------------


def _watch(client) -> str:
    return REGIONS["song-arranger"].watch(client.app.state.svc, "song")


def test_the_watch_moves_when_a_takes_clip_changes_length(client):
    _with_take(client, 1.0)
    before = _watch(client)
    _clip(client, "hit", 2.0)  # "Update clip" writes over it
    assert _watch(client) != before
    # another clip in the store is not this page's business
    before = _watch(client)
    _clip(client, "other", 1.0)
    assert _watch(client) == before


def test_the_watch_follows_the_tempo_and_not_a_step_edit(client):
    before = _watch(client)
    r = post(
        client,
        "/song/pattern/pd/grid",
        {"bars": "1", "steps": "16", "drum.kick.4": "on"},
    )
    assert r.status_code == 200
    assert _watch(client) == before  # the thumbnails are another region's
    client.app.state.studio.song["bpm"] = 100.0  # the cap mark, a take's width
    assert _watch(client) != before


def test_the_arranger_region_still_answers_a_section_edit(client):
    r = post(client, "/song/section/s1/repeats", {"repeats": "3"})
    assert r.status_code == 200 and 'id="song-arranger"' in r.text
    assert '<col class="col-sec" id="col-s1" style="--bars: 2; --reps: 3">' in r.text
