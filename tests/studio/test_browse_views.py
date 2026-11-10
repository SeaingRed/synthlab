"""Library, Breed, Map, Sample Lab, the clip list: what the markup must
keep saying. The layout itself is measured in a real
browser (tests/browser/test_browse_views.py)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio import lab
from synthlab.studio.mapview import bright_pct, map_facts, shelf_rows
from synthlab.studio.server import create_app

TEMPLATES = Path("synthlab/studio/templates")
STATIC = Path("synthlab/studio/static")
MINE = [
    "views/library.html",
    "views/breed.html",
    "views/map.html",
    "views/lab.html",
    "fragments/breed_board.html",
    "fragments/breed_parent.html",
    "fragments/lab_body.html",
    "fragments/lab_chops.html",
    "fragments/lab_job.html",
    "fragments/lab_panel.html",
    "fragments/lab_sources.html",
    "fragments/clip_list.html",
    "fragments/map_body.html",
]


def _src(name: str) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


def _tags(text: str, names: str) -> list[str]:
    return re.findall(rf"<(?:{names})\b[^>]*>", text, re.S)


def test_the_old_button_families_are_gone_from_these_views():
    """One canonical component per kind: no dashed `.ghost`, no orange
    `.use-btn`, no `.empty-hint` paragraph, and every <button> is a `.btn`
    (or a `.seg`'s, a row, an audition surface)."""
    for name in MINE:
        text = _src(name)
        for old in ("ghost", "use-btn", "empty-hint", "steer-row"):
            hit = re.search(rf'class="[^"]*(?<![-\w]){re.escape(old)}(?![-\w])', text)
            assert not hit, (name, old)
    bare = []
    for name in MINE:
        text = _src(name)
        segs = "".join(
            re.findall(r'<span[^>]*class="[^"]*\bseg\b.*?</span>\s*\n', text, re.S)
        )
        for tag in _tags(text, "button"):
            cls = re.search(r'class="([^"]*)"', tag)
            ok = cls and re.search(r"\b(btn|row|audition|menu-item)\b", cls.group(1))
            if not ok and tag not in segs and 'hx-post="/lab/' not in tag:
                bare.append((name, tag[:80]))
    assert not bare, bare


def test_breed_has_exactly_one_primary_and_the_lab_and_library_at_most_one_shown():
    assert _src("views/breed.html").count("btn-primary") == 1
    assert "btn-primary" not in _src("fragments/breed_board.html")
    assert "btn-primary" not in _src("views/map.html") + _src("fragments/map_body.html")
    # Library: the primary is the dock's Use, once a page (it presses the
    # selected card's own, a plain key); the sound already on Synths shows
    # a badge in its place, not a dead primary, and Reload (edited on
    # Synths) is a plain key
    assert _src("views/library.html").count("btn-primary") == 1
    # Lab: the first separation, else the selection's Keep clip — never both
    # (a chop's Keep is not the primary)
    panel, chops = _src("fragments/lab_panel.html"), _src("fragments/lab_chops.html")
    assert "' btn-primary' if loop.first and not src.has_stems" in panel
    assert '\' btn-primary\' if src.has_stems }}" id="lab-keep"' in panel
    assert "btn-primary" not in chops


def test_every_action_of_these_views_is_a_command_or_a_menu_item():
    """`data-cmd` puts an action in the palette, the shortcut sheet and on a
    Stream Deck key; a per-row action (a hundred library rows, 200 clips, 64
    chops) is in its row's context menu instead (`data-menu`), and the
    palette finds sounds and clips by name through the server."""
    missing = []
    for name in MINE:
        for tag in _tags(_src(name), "button|a|summary"):
            acts = (
                "hx-post=" in tag
                or "hx-get=" in tag
                or "data-note=" in tag
                or "download" in tag
            )
            if acts and "data-cmd=" not in tag and "data-menu=" not in tag:
                missing.append((name, re.sub(r"\s+", " ", tag)[:110]))
    # what is left out on purpose: a stem's download links (plain links in a
    # list of names), the confirm inside a clip's delete fold, the Voice and
    # Song placements of a stem, the map's rows (the dots' own load)
    allowed = (
        "lab-dl",
        "cd-dl",
        'hx-post="/fragment/clip/delete"',
        "cd-song",
        "map-row",
        'href="/view/breed"',
        'href="/view/editor"',
    )
    missing = [m for m in missing if not any(a in m[1] for a in allowed)]
    assert not missing, missing
    lab_cmds = re.findall(
        r'data-cmd="([^"]+)"',
        _src("fragments/lab_panel.html")
        + _src("fragments/lab_chops.html")
        + _src("fragments/lab_job.html"),
    )
    assert len(lab_cmds) >= 25
    for must in (
        "Separate into stems",
        "Keep the selection as a clip",
        "Send the selection to the sampler",
        "Cut the selection as a chop",
        "Make a beat from the selection",
        "Place the region on the song",
        "Keep the selected chop as a clip",
        "Set the selection",
        "Cancel the separation",
    ):
        assert any(must in c for c in lab_cmds), must


def test_the_context_menu_island_reads_the_page_and_holds_no_list():
    js = (STATIC / "menu.js").read_text(encoding="utf-8")
    code = js.split("(() => {", 1)[1]
    assert '"[data-menu]"' in code and '"[data-ctx]"' in code and "c.click()" in code
    # the ONE opener: no other island listens for a context menu to open one
    # (grid.js's is the step erase, pen.js's cancels a pen's on its surfaces)
    listeners = {
        p.name
        for p in STATIC.glob("*.js")
        if 'addEventListener("contextmenu"' in p.read_text(encoding="utf-8")
    }
    # ... and steps.js's opens a HIT's menu from a long press or the Menu
    # key on a drum step — the one place a right click is taken (the erase),
    # so menu.js never hears those two there
    assert listeners == {"menu.js", "grid.js", "pen.js", "steps.js"}, listeners
    assert not (STATIC / "ctlmenu.js").exists()
    for other in ("grid.js", "bench.js", "pen.js"):
        assert "showPopover" not in (STATIC / other).read_text(encoding="utf-8")
    assert "hx-post" not in code and "/fragment/" not in code and "/lab/" not in code
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "{{ asset('/static/menu.js') }}\" defer" in base
    for name in (
        "views/library.html",
        "fragments/breed_board.html",
        "fragments/lab_chops.html",
        "fragments/lab_sources.html",
        "fragments/clip_list.html",
    ):
        assert "data-ctx=" in _src(name), name


def test_a_lanes_meter_is_the_stored_peak_over_the_region_at_its_level():
    """Static and honest: nothing is rendered for it."""
    pk = np.zeros(1200, dtype=np.float32)
    pk[100:200] = 0.5  # the song's seconds 1..2 of 12
    pk[600:700] = 1.0
    meta = {
        "seconds": 12.0,
        "region": {"mode": "seconds", "start": 1.0, "length": 1.0},
        "user": {"bpm": None, "offset": None},
        "analysis": {"bpm": 0.0, "downbeat_offset": 0.0, "confidence": 0.0},
    }
    m = lab.region_meter(pk, meta)
    assert m["db"] == -6.0 and not m["clip"] and m["text"] == "−6.0 dBFS"
    assert m["pct"] == round((1 - 6.0206 / 48) * 100)
    loud = lab.region_meter(pk, meta, 9.0)
    assert loud["clip"] and loud["pct"] == 100 and loud["db"] == 3.0
    quiet = lab.region_meter(
        pk, {**meta, "region": {"mode": "seconds", "start": 3.0, "length": 1.0}}
    )
    assert quiet == {"db": None, "text": "silent", "pct": 0, "clip": False}


def test_the_maps_rack_says_the_plot_in_words(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    empty = client.get("/fragment/map").text
    # nothing logged: never an empty well — the shipped sounds,
    # faint, with the sound on Synths among them; no logged row in the list
    assert "map-empty" not in empty and "<svg" in empty
    assert (
        empty.count('class="point shelf') > 250 and 'class="point active here"' in empty
    )
    assert empty.count('class="row map-row') == empty.count("map-near-row") == 8
    client.post("/fragment/breed/generation", data={})
    html = client.get("/fragment/map").text
    assert html.lstrip().startswith('<section id="map-svg" aria-label="Map">')
    # the sound on Synths was never logged: it is measured and plotted as
    # itself ("you are here"), never the newest row standing in for it
    assert (
        html.count('class="row map-row') - html.count("map-near-row") == 8
        and html.count(' row-on"') == 0
    )
    assert html.count('class="here-point"') == 1 and 'class="point active here"' in html
    assert html.count('class="meter"') == 2 and "You are here" in html
    rows = client.app.state.svc.space_store().project_2d(limit=300)
    facts = map_facts(rows)
    assert facts["count"] == 8 and facts["here"]["id"] == rows[-1]["id"]
    shelf = shelf_rows()
    rows = client.app.state.svc.space_store().project_2d(limit=300, background=shelf)
    facts = map_facts(rows)
    assert facts["count"] == 8 and facts["shelf"] == len(shelf)
    assert facts["x_label"] in html and facts["y_label"] in html
    assert [bright_pct(c) for c in (0, 60, 7680, 99999)] == [0, 0, 100, 100]
    assert 40 < bright_pct(700) < 60


DOCK_RAW, DOCK_GZ = 52_000, 9_000  # the keyboard dock, on every page
# the one sound menu (#lane-sounds, base.html): ~370 options,
# 24.7 KB raw / 3.8 KB gzipped, every page — a view swap keeps base.html, so
# Song and Perform reached by the nav need it there; grows with your sounds
MENU_RAW, MENU_GZ = 28_000, 4_500


def test_page_weight_did_not_grow_with_the_denser_library(tmp_path):
    """A row with its docked detail: the row's spectrum is one path (it was
    24 rects) and the detail re-uses it, so a row is no heavier than a card
    was — 354,645 bytes / 41.8 kB gzipped for 108 cards, about
    350,000 / 27.3 kB for 108 rows. Held per row: the shelf grows
    with every style pack (125 rows with the four packs' recipes)."""
    import gzip

    html = TestClient(create_app(data_dir=tmp_path)).get("/view/library").text
    rows = html.count('<use href="#sp-')
    assert rows >= 108 and html.count("<rect") == 0
    # + DOCK: the keyboard dock is every page's (base.html), a fixed
    # 50.7 KB raw / 8.5 KB gzipped that is no row's
    assert len(html.encode()) < rows * 354_645 // 108 + DOCK_RAW + MENU_RAW
    # 32,400 a row: what the shell every page carries adds, not a row —
    # the islands' script tags, the rail, the Colours popover (its early
    # script, the themes' swatches, contrast and black-level rows), the
    # skip links, the name popover, the status strip's song facts, the live
    # vocal panel (a popover on every page, so the mic runs across a view
    # change), the full-screen button, every module's class and stable id
    # (`data-module`), each of the ~77 script and style URLs' own hash
    # (`asset`: an edit re-downloads one file, not all), and recipe cards
    # with longer fx chains and `how` notes; 100,264 against 99,962 at 290
    # rows
    assert len(gzip.compress(html.encode())) < rows * 32_400 // 108 + DOCK_GZ + MENU_GZ


def test_the_shelves_render_rows_with_nothing_measured(tmp_path):
    """A board, a shelf or a map row is drawn from session state and stored
    metadata: a tile saved before a measurement existed (or a bare
    `{"id": ...}`, which tests/studio/test_limits.py puts on the board) draws
    no meter for it — it never fails the fragment. The merged tree did: a
    star on a bare tile was a 500 (`'dict object' has no attribute
    'sub_pct'`)."""
    client = TestClient(create_app(data_dir=tmp_path))
    state = client.app.state.studio
    state.breed_tiles = [
        {"id": "tile0"},
        {"id": "tile1", "name": "half", "centroid": 900},
    ]
    r = client.post("/fragment/breed/star", data={"id": "tile0"})
    assert r.status_code == 200, r.text[-400:]
    assert r.text.count('class="tile ') == 2
    assert ">tile0</span>" in r.text  # an unnamed tile is called by its id
    first, second = r.text.split('data-tile-id="tile1"')
    assert 'class="meter"' not in first  # nothing measured: no meter
    assert second.count('class="meter"') == 1 and "900 Hz" in second
    assert client.get("/view/breed").status_code == 200
    # a library card from a bare entry: every measurement and patch field absent
    env = client.app.state.svc.templates.env
    card = env.get_template("views/library.html").module.card
    for entry in ({"name": "bare"}, {"name": "half", "patch": {"osc1": "saw"}}):
        html = str(card(entry, 0, "library", "bass", False))
        assert 'data-name="' + entry["name"] in html and "--level: 0%" in html
    # the map's rack from rows with no features and no patch
    rows = [{"id": f"{i:012x}", "x": i, "y": i} for i in range(4)]
    facts = map_facts(rows, rows[1]["id"])
    assert facts["here"]["name"] == rows[1]["id"] and facts["here"]["sub_level"] == 0
    html = env.get_template("fragments/map_body.html").render(map=facts, svg="<svg/>")
    assert html.count('class="row map-row') - html.count("map-near-row") == 4
