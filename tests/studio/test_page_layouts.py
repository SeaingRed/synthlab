"""Perform, Drums, Song: what the templates and the two stylesheets must
keep, checked without a browser.

- one primary action a view (`.btn-primary`): the transport's play;
- every action is a command: a control of these views that acts (a post,
  a download, a transport button) declares `data-cmd`, so the palette, the
  shortcut sheet and a Stream Deck find it. What is exempt is listed here
  and is one of: a row of a per-item menu (the menu's opener is the item),
  a control repeated per row / per step whose row is the target (mute, a
  bar chip, a preset), a form's submit beside its field;
- the old button families these views were the last users of are gone
  from the stylesheet, and nothing in the templates wears only them;
- a tweakable value is still a native range with its name and travel.

Does not prove any of it looks right: `tests/browser/test_page_layouts.py`
measures the layout in a real Chromium.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio.server import create_app
from tests.studio.test_song_routes import song_dict, use_song

STATIC = Path("synthlab/studio/static")
VIEWS = ("perform", "drums", "song")

# classes whose control is one of many in a row / a menu: the row is the target
PER_ITEM = {
    "rowbtn",  # mute / solo / arm of one row
    "rowtool",  # a row's tools and the velocity menu's rows
    "lp-star",  # the Loops panel's rows: ★, tag, open, a take's rename
    "lp-tagit",
    "lp-edit",
    "lp-open",  # a row's / a scene's open
    "kt-rename",
    "sl-plus",  # an empty slot's + (keep what the lane plays)
    "barchip",  # 1 2 3 4 bars
    "pill-btn",  # a drum voice's presets
    "menu-item",  # a menu's rows
    "menu-btn",  # the rows of a section / track / take menu and the drawer's tools
    "pt-hint",  # the (?) whose tooltip is the how-to
    "pool-open-pattern",
    "cell-btn",
    "take-name",
    "track-name",
    "pick",
    "kbd-mode",
    "hit-pad",
    "fold",
    "load",  # a saved beat's card (B1's sound-card)
    "fxc-row",  # a chain in the chain browser (its opener is the command)
}


class _Controls(HTMLParser):
    """every <button> / <a> / <summary> under #view, with its attributes"""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.found: list[dict] = []
        self.skip = 0  # inside the shell's own fragments (reset menu, head help)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id") == "view":
            self.depth = 1
        elif self.depth and tag not in (
            "input",
            "br",
            "hr",
            "img",
            "use",
            "path",
            "circle",
            "rect",
            "col",
            "polyline",
        ):
            self.depth += 1
        if self.depth and tag in ("button", "a", "summary"):
            self.found.append({"tag": tag, **a})

    def handle_endtag(self, tag):
        if self.depth and tag not in (
            "input",
            "br",
            "hr",
            "img",
            "use",
            "path",
            "circle",
            "rect",
            "col",
            "polyline",
        ):
            self.depth -= 1


def _client(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    use_song(client, song_dict())
    return client


def _controls(html: str) -> list[dict]:
    p = _Controls()
    p.feed(html)
    return p.found


@pytest.mark.parametrize("view", VIEWS)
def test_a_view_has_one_primary_action_and_it_is_the_transport(tmp_path, view):
    html = _client(tmp_path).get(f"/view/{view}").text
    main = html[html.index('id="view"') :]
    primaries = re.findall(
        r'<(?:button|a)\b[^>]*class="[^"]*\bbtn-primary\b[^"]*"[^>]*>', main
    )
    assert len(primaries) == 1, primaries
    assert 'id="song-play"' in primaries[0] or 'id="play"' in primaries[0]


@pytest.mark.parametrize("view", VIEWS)
def test_every_action_of_the_view_is_a_command(tmp_path, view):
    html = _client(tmp_path).get(f"/view/{view}").text
    missing = []
    for c in _controls(html):
        classes = set((c.get("class") or "").split())
        acts = (
            any(k in c for k in ("hx-post", "hx-get", "data-song-action", "download"))
            or c.get("type") == "submit"
        )
        if not acts or "data-cmd" in c or classes & PER_ITEM:
            continue
        if (
            c.get("popovertarget")
            or "reset" in " ".join(classes)
            or "head-help" in classes
        ):
            continue
        if c.get("value") in (
            "unique",
            "shared",
            "",
        ):  # the "record into a shared pattern?" dialog's answers (and Cancel)
            continue
        if (
            c["tag"] == "summary"
            or "sound-card" in html
            and c.get("hx-post", "").startswith("/fragment/drums/lib/delete")
        ):
            continue
        missing.append(
            {k: c[k] for k in ("class", "hx-post", "id", "aria-label") if k in c}
        )
    assert not missing, missing


def test_commands_carry_their_shortcuts(tmp_path):
    client = _client(tmp_path)
    song = client.get("/view/song").text
    for cmd, keys in (
        ("Play the song", "Space"),
        ("Loop the selected section", "L"),
        ("Undo the last song edit", "Ctrl+Z"),
    ):
        assert re.search(
            rf'data-cmd="{cmd}"[^>]*data-keys="{re.escape(keys)}"', song
        ), cmd
    perform = client.get("/view/perform").text
    assert re.search(
        r'data-cmd="Play / stop the loop"[^>]*data-keys="Space"', perform, re.S
    )
    assert re.search(r'id="pattern-undo" data-keys="Ctrl\+Z"', perform)
    # the momentary FX are keys, not commands: they are named for the sheet
    assert (
        perform.count('class="btn pfx"') == 7
        and perform.count("(hold)") == 7
        and 'data-keys="C" data-key-label="Tape stop (hold)"' in perform
    )


def test_the_old_families_are_gone_from_the_stylesheets():
    css = re.sub(
        r"/\*.*?\*/", "", (STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S
    )
    for family in (
        ".stbtn",
        ".menu-btn",
        ".pt",
        ".rowtool",
        ".pfx",
        ".barchip",
        ".pill-btn",
        ".song-mode span",
        ".sec-menu-btn",
        ".drawer-close",
        ".pool-add",
        ".song-note-close",
    ):
        rules = re.findall(
            rf"(?m)^{re.escape(family)}(?:[:.\[][^,{{]*)? \{{[^}}]*\}}", css
        )
        skins = [
            r
            for r in rules
            if re.search(
                r"\b(border-radius|padding|min-height|height|font-size)\s*:", r
            )
        ]
        assert not skins, (family, skins)
    # ... and the row buttons keep only their lit colours
    assert not re.search(r"(?m)^\.rowbtn \{[^}]*(width|height|border)", css)


@pytest.mark.parametrize("view", VIEWS)
def test_no_control_of_the_view_wears_only_an_old_family(tmp_path, view):
    html = _client(tmp_path).get(f"/view/{view}").text
    old = {
        "stbtn",
        "menu-btn",
        "pt",
        "rowtool",
        "pfx",
        "pill-btn",
        "sec-menu-btn",
        "drawer-close",
        "pool-add",
        "ghost",
    }
    bare = []
    for c in _controls(html):
        classes = set((c.get("class") or "").split())
        if classes & old and not classes & {"btn", "menu-item"}:
            bare.append(c.get("class"))
    assert not bare, bare


def test_the_knobs_are_native_ranges_with_their_contract(tmp_path):
    client = _client(tmp_path)
    song = client.get("/view/song").text
    gains = re.findall(
        r'<input type="range" class="track-gain knob knob-s"[^>]*>', song
    )
    assert gains, "no track level knob"
    for g in gains:
        for part in (
            'name="gain"',
            'min="0"',
            'max="1.5"',
            'step="0.05"',
            'data-default="1"',
            "hx-post=",
            "--fill:",
        ):
            assert part in g, (part, g)
    drums = client.get("/view/drums").text
    knobs = re.findall(r'<input type="range" class="knob" id="d-[^"]+"[^>]*>', drums)
    assert len(knobs) >= 8 * 3  # every voice: its shape, its level, its chance
    assert all("data-default=" in k and "name=" in k and "--fill:" in k for k in knobs)
    assert 'class="knob knob-s" data-default="0" name="swing"' in drums


def test_the_song_page_draws_its_signal_path_and_says_where_levels_come_from(tmp_path):
    html = _client(tmp_path).get("/view/song").text
    n = html.count('class="arr-track')
    assert n >= 2
    # a lead at every track head, one into the master strip a track, one meter
    assert html.count('class="track-path"') == n
    master = html[html.index('id="song-master"') :]
    master = master[: master.index("</aside>")]
    assert master.count("<path") >= n and master.count('<li class="master-in') == n
    assert master.count('class="meter meter-v master-meter"') == 1
    # the menus a right click opens are declared on what is pressed
    assert (
        html.count('data-ctx-pop="cell-menu"') >= n
        and 'id="cell-menu" class="pop menu ctx-menu" popover' in html
    )
    assert re.search(r'class="arr-section"[^>]*data-ctx-pop="secmenu-', html, re.S)
    assert re.search(r'class="track-head" data-ctx-pop="trkmenu-', html)


def test_the_dense_tier_tiles_the_three_views_to_the_window():
    dense = (STATIC / "dense.css").read_text(encoding="utf-8")
    d18 = dense[
        dense.index("D18. The loop pages") : dense.index("D19. The browsing pages")
    ]
    for view in VIEWS:
        if view == "perform":
            # Loops' tiling is in the later fine-pointer block, with the
            # lane inspector's rows
            assert re.search(
                r"#view\.perform-view \{ height: "
                r"calc\(100dvh - var\(--top-h\) - var\(--keys-h\)\)",
                dense,
            ), view
            continue
        # Drums: at least the window (a short window scrolls
        # the page rather than squash the step grid to its kick row)
        tall = "height: auto; min-height" if view == "drums" else "height"
        assert re.search(
            # (- --keys-h: the keyboard dock is every page's)
            rf"\.{view}-view \{{ {tall}: "
            r"calc\(100dvh - var\(--top-h\) - var\(--keys-h\)\)",
            d18,
        ), view
    # the lanes and the steps share their pane's height; the editor docks
    assert "container: arr / size" in d18 and "container: steps / size" in d18
    assert ".song-drawer:not([hidden]) + .drawer-idle { display: none; }" in d18
