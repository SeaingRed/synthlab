"""The Song view: layout A's four regions, the nav
entry, the drawer's shared grids, the one-time migration note and the
track-edit banner.

Layout at both breakpoints is pinned in test_style.py; the grid macros'
byte-identity on Drums and Perform in test_grid_macros.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import StrictUndefined
from starlette.requests import Request

from synthlab.studio import schema
from synthlab.studio.app import make_templates
from synthlab.studio.server import create_app
from synthlab.studio.service import ALL, REGIONS, SONG_NEEDS
from tests.studio.test_song_routes import song_dict, use_song

FIXTURES = Path(__file__).parents[1] / "fixtures" / "legacy_states"
TEMPLATES = Path(__file__).parents[2] / "synthlab" / "studio" / "templates"
SONG_REGIONS = (
    "song-topbar",
    "song-pool",
    "song-arranger",
    "song-drawer",
    "song-thumbs",
)
NEW_TEMPLATES = (
    "views/song.html",
    "fragments/song_topbar.html",
    "fragments/song_pool.html",
    "fragments/song_arranger.html",
    "fragments/song_drawer.html",
    "fragments/song_thumbs.html",
    "fragments/track_edit_banner.html",
    "grids.html",
)
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/song"}


def _migrated(tmp_path: Path, name: str = "with_scenes") -> TestClient:
    """A session loaded from a real frozen v2 state (migrated on load)."""
    (tmp_path / "state.json").write_text(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return TestClient(create_app(data_dir=tmp_path))


def _req() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


# -- the page ---------------------------------------------------------------------


def test_the_view_renders_a_migrated_song(tmp_path):
    client = _migrated(tmp_path)
    song = client.app.state.studio.song
    assert song["sections"] and song["tracks"] and song["patterns"]
    html = client.get("/view/song").text
    for sec in song["sections"]:
        assert f'id="sec-{sec["id"]}"' in html
        for t in song["tracks"]:
            assert f'id="cell-{sec["id"]}-{t["id"]}"' in html
    for t in song["tracks"]:
        assert f'id="trk-{t["id"]}"' in html
    for pid in song["patterns"]:
        assert f'id="pool-{pid}"' in html
    assert html.count('class="arr-section"') == len(song["sections"])
    # a synth track's name opens its sound on Synths
    synth = next(t for t in song["tracks"] if t["kind"] == "synth")
    assert f'hx-post="/song/track/{synth["id"]}/edit-sound"' in html


def test_every_region_id_is_on_the_page(tmp_path):
    client = _migrated(tmp_path)
    html = client.get("/view/song").text
    for rid in SONG_REGIONS:
        assert html.count(f'id="{rid}"') == 1, rid
    assert 'id="track-edit-banner"' in client.get("/view/editor").text


def test_the_nav_puts_song_between_perform_and_mix(client):
    html = client.get("/view/song").text
    nav = html[html.index('<nav class="views"') : html.index("</nav>")]
    labels = re.findall(r'href="/view/(\w+)"', nav)
    # the rail by job: Synths, Drums, Perform, Song, Mix
    assert labels[:5] == ["editor", "drums", "perform", "song", "mixer"]
    assert 'href="/view/song"\n' in nav or 'aria-current="page"' in nav
    active = re.search(r'<a class="view active" href="/view/(\w+)"', nav)
    assert active and active.group(1) == "song"


# -- every fragment under StrictUndefined -----------------------------------------


def _strict_env(client):
    env = make_templates().env.overlay(undefined=StrictUndefined)
    env.globals.update(client.app.state.svc.templates.env.globals)
    return env


DRAWERS = {
    "closed": None,
    "drum cell": {"section_id": "s1", "track_id": "td"},
    "note cell": {"section_id": "s2", "track_id": "t2"},
    "empty cell": {"section_id": "s1", "track_id": "t2"},
    "pool pattern": {"pattern_id": "p3"},
}


@pytest.mark.parametrize("drawer", sorted(DRAWERS))
@pytest.mark.parametrize("oob", [False, True])
def test_each_fragment_renders_under_strict_undefined(client, drawer, oob):
    s = client.app.state.studio
    s.song_drawer = DRAWERS[drawer]
    s.edit_track("t1")  # the banner's full branch
    env = _strict_env(client)
    ctx = client.app.state.svc.page_context(_req(), "song", need=SONG_NEEDS)
    for rid in (*SONG_REGIONS, "track-edit-banner"):
        html = env.get_template(REGIONS[rid].template).render({**ctx, "oob": oob})
        assert f'id="{rid}"' in html
        # the drawer comes back as a morph: replaced, an automation slider
        # lost keyboard focus on every change (tests/browser/test_song.py)
        # so does the topbar: its undo / redo buttons redraw themselves
        morphs = rid in ("song-drawer", "song-topbar")
        how = "outerMorph" if morphs else "outerHTML"
        assert (f'hx-swap-oob="{how}"' in html) is oob
        assert html.count("hx-swap-oob=") == (1 if oob else 0)
    # a page renders its regions with no `oob` (a lenient env reads it as
    # false; the reset menu relies on that too)
    full = {**client.app.state.svc.page_context(_req(), "song", need=ALL), "oob": False}
    assert 'id="song-arranger"' in env.get_template("views/song.html").render(full)


def test_an_empty_song_renders_under_strict_undefined(client):
    use_song(client, {"bpm": 120.0, "root": "A1", "scale_name": "minor"})
    env = _strict_env(client)
    ctx = client.app.state.svc.page_context(_req(), "song", need=ALL)
    html = env.get_template("views/song.html").render({**ctx, "oob": False})
    assert "No sections yet" in html and 'id="song-play"' in html
    assert re.search(r'id="song-play"[^>]*disabled', html)


def test_the_regions_need_only_the_song_section(client):
    """A song route redraws its regions from a lean context (`SONG_NEEDS`):
    they must render exactly what a full page's context renders."""
    svc = client.app.state.svc
    client.app.state.studio.song_drawer = DRAWERS["drum cell"]
    for rid in (*SONG_REGIONS, "track-edit-banner"):
        tpl = svc.templates.get_template(REGIONS[rid].template)
        lean = tpl.render(svc.page_context(_req(), "song", need=SONG_NEEDS))
        full = tpl.render(svc.page_context(_req(), "song", need=ALL))
        assert lean == full, rid
    # and a page that shows none of them no longer builds the song context
    assert "song_usage" not in svc.page_context(_req(), "drums", need=frozenset())


# -- the drawer -------------------------------------------------------------------


def _drawer(client, **form) -> str:
    r = client.post("/song/drawer", data=form, headers=HX)
    assert r.status_code == 200
    return r.text


def test_the_drawer_opens_a_drum_cell_on_the_shared_drum_grid(client):
    html = _drawer(client, section_id="s1", track_id="td")
    assert 'id="song-drawer"' in html and " hidden" not in html.split(">")[0]
    assert '<form id="drawer-grid" class="drum-grid"' in html
    assert 'hx-post="/song/pattern/pd/grid"' in html
    assert 'name="drum.kick.0"' in html and 'name="dvel.0"' in html
    # checked where the pattern has hits (kick 0, snare 8)
    assert re.search(r'name="drum\.kick\.0"\s+class="[^"]*"\s+checked', html)
    # the row heads carry no mixer: those buttons drive the working loop
    assert (
        "/fragment/drums/row/" not in html
        and "drum-grid-8 .rowbtn" not in html
        and html.count('rowbtn mute"') == html.count('data-mix="mute"')
    )
    assert (
        'hx-post="/song/cell/unique"' in html
        and 'hx-post="/song/cell/variation"' in html
    )


def test_the_drawer_opens_a_note_cell_on_the_shared_note_grid(client):
    html = _drawer(client, section_id="s1", track_id="t1")
    assert '<form id="drawer-grid" class="drawer-notes"' in html
    assert 'hx-post="/song/pattern/p1/grid"' in html
    assert 'aria-label="Note grid"' in html
    assert 'name="cell.A1.0"' in html and 'name="slide.0"' in html
    assert 'name="arp_mode"' in html
    assert "outside the key" not in html


def test_the_drawer_counts_notes_outside_the_key(client):
    song = song_dict()
    song["patterns"]["p1"]["cells"] = [["A1", 0], ["C#3", 2], ["F#3", 4]]
    use_song(client, song)
    html = _drawer(client, section_id="s1", track_id="t1")
    assert 'data-hidden-count="2"' in html and "2 notes outside the key" in html


def test_the_drawer_posts_what_the_grid_route_parses(client):
    """The drawer's own fields round-trip through /song/pattern/{id}/grid:
    ticking a step persists in the pattern (not the working loop)."""
    html = _drawer(client, section_id="s1", track_id="td")
    form = dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', html))
    form |= {"drum.kick.0": "on", "drum.snare.8": "on", "drum.hat.2": "on"}
    loop = client.app.state.studio.arrangement.drums
    assert (
        client.post("/song/pattern/pd/grid", data=form, headers=HX).status_code == 200
    )
    assert ["hat", 2] in client.app.state.studio.song["patterns"]["pd"]["drums"]
    assert client.app.state.studio.arrangement.drums == loop


def test_an_empty_cell_offers_the_matching_patterns(client):
    html = _drawer(client, section_id="s1", track_id="t2")
    assert "Empty cell" in html and 'hx-post="/song/cell"' in html
    opts = re.findall(r'<option value="(\w*)"', html)
    assert set(opts) == {"", "p1", "p2", "p3"}  # note patterns only
    assert 'id="drawer-grid"' not in html


def test_a_pool_pattern_can_be_renamed_and_deleted_from_the_drawer(client):
    html = _drawer(client, pattern_id="p3")
    assert "from the pool" in html
    assert 'hx-post="/song/pattern/p3/rename"' in html
    assert 'hx-post="/song/pattern/p3/delete"' in html
    assert 'hx-post="/song/pattern/p3/duplicate"' in html
    # a cell's drawer forks through the cell instead (Make unique / Variation)
    cell = _region(_drawer(client, section_id="s1", track_id="t1"), "song-drawer")
    assert "/song/pattern/p1/duplicate" not in cell and "/delete" not in cell


# -- pattern making: thumbnails, shared patterns, the empty cell -------------------


def _region(html: str, rid: str) -> str:
    """One region out of a page or an OOB answer (up to the next region)."""
    start = html.index(f'id="{rid}"')
    ends = [html.find(f'id="{r}"', start + 1) for r in (*SONG_REGIONS, "reset-menu")]
    return html[start : min([e for e in ends if e > start], default=len(html))]


def _cell(html: str, sid: str, tid: str) -> str:
    start = html.index(f'id="cell-{sid}-{tid}"')
    return html[start : html.index("</td>", start)]


def test_every_pattern_has_one_thumbnail_symbol(client):
    """The thumbnails are drawn once, as symbols in their own region: a mark
    per hit or note, in the pattern's own steps x rows box."""
    thumbs = _region(client.get("/view/song").text, "song-thumbs")
    for pid in ("pd", "p1", "p2", "p3"):
        assert thumbs.count(f'<symbol id="thumb-{pid}"') == 1, pid
    pd = thumbs[thumbs.index('id="thumb-pd"') :].split("</symbol>")[0]
    assert 'viewBox="0 0 16 8"' in pd and pd.count("<rect") == 2
    assert '<rect x="0" y="0" width="1" height="1"/>' in pd  # kick, step 0
    assert '<rect x="8" y="1" width="1" height="1"/>' in pd  # snare, step 8
    p3 = thumbs[thumbs.index('id="thumb-p3"') :].split("</symbol>")[0]
    assert "<rect" not in p3  # Spare is empty
    assert 'aria-hidden="true"' in thumbs.split(">")[0]


def test_a_filled_cell_draws_its_pattern_once_per_time_it_plays(client):
    html = _region(client.get("/view/song").text, "song-arranger")
    # Beat is 1 bar in the 2-bar Intro: its thumbnail twice
    beat = _cell(html, "s1", "td")
    assert 'class="cell-name">Beat<' in beat
    assert 'viewBox="0 0 2.0 1"' in beat
    assert beat.count('<use href="#thumb-pd"') == 2
    assert '<use href="#thumb-pd" x="1" y="0" width="1" height="1"/>' in beat
    # Hook is 2 bars in the 2-bar Verse: once
    hook = _cell(html, "s2", "t2")
    assert hook.count('<use href="#thumb-p2"') == 1 and 'viewBox="0 0 1.0 1"' in hook
    # an empty cell draws no thumbnail, and says it can be filled
    empty = _cell(html, "s1", "t2")
    assert "<use" not in empty and "cell-add" in empty
    assert 'aria-label="Intro, Lead: empty"' in empty


def test_a_pattern_that_does_not_divide_its_section_is_cut_not_squeezed(client):
    song = song_dict()
    song["patterns"]["p2"]["bars"] = 2
    song["sections"][1]["bars"] = 3
    use_song(client, song)
    hook = _cell(_region(client.get("/view/song").text, "song-arranger"), "s2", "t2")
    assert 'viewBox="0 0 1.5 1"' in hook and hook.count('<use href="#thumb-p2"') == 2


def test_a_shared_pattern_says_so_on_its_cells_and_in_the_drawer(client):
    html = client.get("/view/song").text
    arr = _region(html, "song-arranger")
    for sid in ("s1", "s2"):  # Beat and Riff play in both sections
        for tid in ("td", "t1"):
            cell = _cell(arr, sid, tid)
            assert 'class="cell-shared"' in cell and "×2" in cell, (sid, tid)
            assert "shared by 2 sections" in cell  # the button's accessible name
    hook = _cell(arr, "s2", "t2")  # only Verse plays Hook
    assert "cell-shared" not in hook and "shared by" not in hook
    # the drawer names the sections an edit changes, and offers the fork
    drawer = _region(_drawer(client, section_id="s1", track_id="t1"), "song-drawer")
    assert "shared · edits change 2 sections" in drawer
    assert 'title="Intro, Verse"' in drawer
    unique = re.search(r"<button[^>]*/song/cell/unique[^>]*>", drawer).group(0)
    assert "disabled" not in unique and "primary" in unique
    # a pattern only this cell plays is its own already: nothing to fork
    drawer = _region(_drawer(client, section_id="s2", track_id="t2"), "song-drawer")
    assert "used in 1 section" in drawer and "shared ·" not in drawer
    unique = re.search(r"<button[^>]*/song/cell/unique[^>]*>", drawer).group(0)
    assert "disabled" in unique and "primary" not in unique


def test_the_shared_notice_follows_a_section_rename(client):
    """The drawer names the sections sharing its pattern, so its watch has
    to see a rename (test_oob_refresh holds every route to this)."""
    _drawer(client, section_id="s1", track_id="t1")
    r = client.post("/song/section/s2/rename", data={"name": "Drop"}, headers=HX)
    assert 'title="Intro, Drop"' in _region(r.text, "song-drawer")


def test_an_empty_cell_is_filled_in_one_click(client):
    """An empty cell's drawer: a new pattern of the track's kind in this
    cell, or one of the pool's by name and thumbnail — each a button
    (keyboard and touch alike), not only the <select>."""
    drawer = _region(_drawer(client, section_id="s1", track_id="t2"), "song-drawer")
    new = re.search(r'<button[^>]*class="pick pick-new"[^>]*>[^<]*', drawer).group(0)
    assert 'hx-post="/song/cell/new"' in new and "New note pattern" in new
    picks = re.findall(r'<button type="button" class="pick" [^>]*>', drawer)
    assert len(picks) == 3  # the note patterns; never the beat
    for pid in ("p1", "p2", "p3"):
        assert f'"pattern_id": "{pid}"' in drawer, pid
        assert f'<use href="#thumb-{pid}"' in drawer
    assert "thumb-pd" not in drawer
    assert 'aria-label="Use Hook here (2 bars)"' in drawer
    # on the drums track it offers a beat
    drawer = _region(_drawer(client, section_id="s1", track_id="td"), "song-drawer")
    assert "pick-new" not in drawer  # a filled cell: "+ New" sits with its tools
    assert 'aria-label="New empty pattern for this cell"' in drawer
    assert 'aria-label="Empty this cell"' in drawer


def test_the_grid_sits_right_under_the_tools_and_automation_after_it(client):
    """The drawer's order: head, the cell's actions, one rack (length +
    tools), the hint, the grid — then the automation lanes. They used to
    sit between the tools and the grid and push it off a laptop screen."""
    client.post(
        "/song/cell/auto",
        data={"section_id": "s1", "track_id": "t1", "op": "add", "target": "gain"},
        headers=HX,
    )
    drawer = _region(_drawer(client, section_id="s1", track_id="t1"), "song-drawer")
    order = [
        drawer.index(mark)
        for mark in (
            'class="drawer-head"',
            'class="drawer-assign"',
            'class="drawer-rack"',
            'class="drawer-shape"',
            'role="toolbar"',
            "drawer-hint",
            'id="drawer-grid"',
            'class="drawer-lanes"',
            'class="lane-row"',
        )
    ]
    assert order == sorted(order)
    assert "Shift-click a step" in drawer and "right-click erases" in drawer
    # a pool pattern's drawer has the rack and the grid, no lanes
    drawer = _region(_drawer(client, pattern_id="p1"), "song-drawer")
    assert 'class="drawer-rack"' in drawer and "drawer-lanes" not in drawer


def test_the_arranger_says_what_to_do_next_when_it_is_empty(client):
    song = song_dict()
    use_song(client, {**song, "tracks": [], "sections": [], "patterns": {}})
    assert "No sections yet" in client.get("/view/song").text
    bare = {"id": "s1", "name": "Intro", "bars": 2, "cells": {}}
    use_song(client, {**song, "tracks": [], "sections": [bare], "patterns": {}})
    assert "No tracks yet" in client.get("/view/song").text
    use_song(client, {**song, "sections": [bare], "patterns": {}})
    page = client.get("/view/song").text
    assert "Click an empty cell" in page and "No tracks yet" not in page
    use_song(client)  # a song with patterns needs no hint
    assert "empty-hint" not in _region(client.get("/view/song").text, "song-arranger")


def test_a_step_edit_resends_the_thumbnails_not_the_arranger(client):
    """A cell's thumbnail is a <use> of the pattern's symbol, so a note
    drawn in the drawer re-sends the small `song-thumbs` region (and the
    pool) — never the arranger, whose scroll and open menus a swap resets."""
    _drawer(client, section_id="s1", track_id="td")
    form = {"bars": "1", "steps": "16", "drum.kick.0": "on", "drum.hat.6": "on"}
    r = client.post("/song/pattern/pd/grid", data=form, headers=HX)
    assert r.status_code == 200
    ids = set(re.findall(r'id="(song-[a-z]+)"[^>]*hx-swap-oob', r.text))
    assert "song-thumbs" in ids and "song-arranger" not in ids
    assert '<rect x="6" y="2" width="1" height="1"/>' in _region(r.text, "song-thumbs")


def test_user_text_is_escaped_everywhere(client):
    song = song_dict()
    evil = '<img src=x onerror="alert(1)">'
    song["tracks"][1]["name"] = evil
    song["patterns"]["p1"]["name"] = evil
    song["sections"][0]["name"] = evil
    use_song(client, song)
    client.app.state.studio.song_drawer = {"section_id": "s1", "track_id": "t1"}
    html = client.get("/view/song").text
    assert evil not in html
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in html


# -- the one-time migration note --------------------------------------------------


def test_the_migration_note_shows_once_escaped_then_clears(tmp_path):
    client = _migrated(tmp_path, "divergent")
    s = client.app.state.studio
    note = s.song["migration_note"]
    assert note and "c (" in note
    html = client.get("/view/song").text
    toast = html[html.index('class="toast song-note"') :][:2000]
    assert 'hx-post="/song/migration-note/clear"' in toast
    assert 'hx-trigger="load"' in toast
    assert "state.json.v2.bak" in toast
    assert not s.is_dirty("song")
    r = client.post("/song/migration-note/clear", headers=HX)
    assert r.status_code == 200 and r.text == ""
    assert s.song["migration_note"] == ""
    assert not s.is_dirty("song")  # the baseline lost it too: no "changed" dot
    assert "song-note" not in client.get("/view/song").text
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["song"]["migration_note"] == ""
    # a second clear is harmless
    assert client.post("/song/migration-note/clear", headers=HX).status_code == 200


def test_the_migration_note_is_autoescaped(client):
    song = schema.conform(song_dict(), schema.nodes().SONG)
    song["migration_note"] = "scenes <b>x</b> & <script>alert(1)</script>"
    client.app.state.studio.song = song
    html = client.get("/view/song").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_no_migration_note_no_toast(client):
    assert "song-note" not in client.get("/view/song").text.split("<main", 1)[1]


# -- the track-edit banner --------------------------------------------------------


def test_the_banner_says_leaving_synths_ends_the_edit(client):
    client.post("/song/track/t1/edit-sound", headers=HX)
    page = client.get("/view/editor").text
    banner = page[page.index('id="track-edit-banner"') :][:1500]
    assert "Leaving Synths ends the edit" in banner
    assert "into the loop, not the track" in banner


# -- security ---------------------------------------------------------------------


@pytest.mark.parametrize("name", NEW_TEMPLATES)
def test_the_new_templates_carry_no_script_and_nothing_marked_safe(name):
    src = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "|safe" not in src.replace(" ", "")
    assert "<script" not in src
    assert "hx-on" not in src and "js:" not in src
    assert not re.search(r"\son[a-z]+=", src)  # no inline handlers
