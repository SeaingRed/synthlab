"""Every view's form markup must reference real controls.

A `label for=` / `aria-labelledby` / `aria-controls` / `output for=` pointing
at an id that isn't on the page is invalid HTML *and* silently drops the
control's accessible name — the pills radiogroups shipped that way (the
`<label for="p-osc1">` had no `#p-osc1` to point at, because pills render a
radiogroup rather than one control).
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from synthlab.studio.server import create_app

VIEWS = [
    "/",
    "/view/drums",
    "/view/perform",
    "/view/breed",
    "/view/map",
    "/view/library",
    "/view/bass",
]

# attribute -> whether it holds a space-separated id list
REFERENCING_ATTRS = {
    "for": True,  # <label for>, <output for="a b">
    "aria-labelledby": True,
    "aria-describedby": True,
    "aria-controls": True,
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(data_dir=tmp_path_factory.mktemp("studio"))
    with TestClient(app) as c:
        yield c


def _ids(html: str) -> set[str]:
    return set(re.findall(r'\sid="([^"]+)"', html))


def _refs(html: str, attr: str, multi: bool) -> list[str]:
    out: list[str] = []
    for value in re.findall(rf'\s{attr}="([^"]*)"', html):
        out.extend(value.split() if multi else [value])
    return out


@pytest.mark.parametrize("view", VIEWS)
def test_no_dangling_id_references(client, view):
    html = client.get(view).text
    ids = _ids(html)
    dangling = {
        attr: sorted({r for r in _refs(html, attr, multi) if r not in ids})
        for attr, multi in REFERENCING_ATTRS.items()
    }
    dangling = {k: v for k, v in dangling.items() if v}
    assert not dangling, f"{view} references ids that do not exist: {dangling}"


@pytest.mark.parametrize("view", VIEWS)
def test_ids_are_unique(client, view):
    html = client.get(view).text
    seen: dict[str, int] = {}
    for i in re.findall(r'\sid="([^"]+)"', html):
        seen[i] = seen.get(i, 0) + 1
    dupes = sorted(k for k, n in seen.items() if n > 1)
    assert not dupes, f"{view} has duplicate ids: {dupes}"


@pytest.mark.parametrize("view", VIEWS)
def test_no_nested_forms(client, view):
    """A <form> inside a <form> is invalid and the inner one is dropped by
    the parser, so its fields silently post to the outer action."""
    html = client.get(view).text
    depth = 0
    for tag in re.findall(r"<(/?)form\b", html):
        depth += -1 if tag == "/" else 1
        assert depth <= 1, f"{view} nests a <form> inside another <form>"
    assert depth == 0, f"{view} has unbalanced <form> tags"


@pytest.mark.parametrize("view", VIEWS)
def test_radiogroups_are_named(client, view):
    """Each pills group is a role=radiogroup; without a name a screen reader
    announces a bare 'radio group' with no idea which parameter it sets."""
    html = client.get(view).text
    for group in re.findall(r"<div[^>]*role=\"radiogroup\"[^>]*>", html):
        assert "aria-labelledby=" in group or "aria-label=" in group, (
            f"{view} has an unnamed radiogroup: {group}"
        )


def _page(tmp_path):
    from pathlib import Path as _P  # noqa: F401

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    return TestClient(create_app(data_dir=tmp_path)).get("/").text


def test_cutoff_renders_as_log_position_with_hidden_value(tmp_path):
    """A linear 60-8000 slider gives the musical 60-500 Hz region ~5% of the
    travel. Log fields render a 0..1 position slider plus a
    hidden field carrying the real value the form posts."""
    html = _page(tmp_path)
    row = html.split('id="row-cutoff"')[1].split("</div>")[0]
    assert 'data-log="1"' in row
    assert 'type="hidden" name="cutoff"' in row
    assert "data-default" in row and 'data-unit="Hz"' in row


def test_log_slider_posts_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        client.post("/fragment/patch", data={"cutoff": "500"})
        assert client.app.state.studio.patch.cutoff == 500.0
        assert 'value="500' in client.get("/").text  # hidden field shows it


# One literal contract per lane, named directly rather than built from
# state.lane_fields() — deriving a test's expectation from the very
# function whose wiring it's meant to pin would let a wrong `lane` tag
# shrink the check right along with the (buggy) keep-set it's checking,
# so the mistake would be invisible to the test that's supposed to catch
# it. test_non_synth_field_contracts_match_lane_tags (below) is the
# explicit companion that pins each of these tuples against its
# lane_fields()-derived set, so the two can't quietly diverge either —
# a new field in one of these lanes forces this file to be touched.
_DRUM_AND_CLIP_FIELDS = (
    "drums",
    "drum_params",
    "drum_bars",
    "drum_steps",
    "drum_swing",
    "drum_humanize",
    "drum_vel",
    "drum_samples",
    "drum_mix",
    "clips",
    "drum_hits",
    "drum_row_swing",
)
_CELL_FIELDS = ("cells", "velocities", "slides", "cuts", "note_chance")
_GRID_FIELDS = ("bars", "steps_per_bar", "swing", "arp_mode", "arp_octaves", "strum")
# (the bar and a section's tempo ramp, meter.py: shared by both grids)
_SHARED_FIELDS = ("bpm", "root", "scale_name", "meter", "ramp")


def test_non_synth_field_contracts_match_lane_tags():
    """The literal contracts above must name exactly the fields tagged
    with the corresponding lane — otherwise a field added to (or moved
    out of) a lane could silently stop being covered by the behavioural
    tests below without either list changing."""
    from synthlab.studio.state import lane_fields

    assert set(_DRUM_AND_CLIP_FIELDS) == lane_fields("drum") | lane_fields("clip")
    assert set(_CELL_FIELDS) == lane_fields("cell")
    assert set(_GRID_FIELDS) == lane_fields("grid")
    assert set(_SHARED_FIELDS) == lane_fields("shared")


def _fill_non_synth_arrangement(s):
    """Give every drum/clip/cell/grid field a distinctive, non-default
    value, so a route that resets one to its default is observable. The
    "shared" fields (bpm/root/scale_name) are deliberately left alone —
    tests that need to prove those come from the posted form set them
    explicitly, to a value distinct from both the default and whatever
    this leaves them at."""
    from dataclasses import replace

    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["snare", 4]],
        drum_params={"kick": {"tune": 40.0}},
        drum_bars=2,
        drum_steps=8,
        drum_swing=0.3,
        drum_humanize=0.5,
        drum_vel={"0": 0.4},
        drum_samples={"kick": "chop"},
        drum_mix={"kick": {"level": 0.5}},
        drum_hits={"snare.4": {"v": 0.3, "r": 2}, "kick.0": {"n": -0.25}},
        drum_row_swing={"kick": 0.0},
        clips=[{"clip": "take", "at_bar": 1, "gain": 0.8}],
        cells=[["C2", 1]],
        cuts=[["C2", 1]],
        velocities={"1": 0.4},
        note_chance={"1": 0.25},
        slides=[1],
        bars=2,
        steps_per_bar=8,
        swing=0.4,
        arp_mode="up",
        arp_octaves=2,
        strum=15.0,
    )
    return s.arrangement


def test_synth_form_post_preserves_every_non_synth_field(tmp_path):
    """A Synths-bench slider post (`/fragment/patch`) must never disturb the
    drum machine, the clip lane, the note grid, or the synth grid's own
    shape/feel (bars/steps/swing/arp — none of it is on the Synths form).
    Every Arrangement field in those lanes starts at its own (often empty)
    default, so leaving it untouched would be indistinguishable from
    silently resetting it to that same default — fill each one with a
    distinctive value first, so preservation is actually observable.

    The #patch form carries hx-include="#patch, #sound-bar"
    (editor.html/sound_bar.html), so every *real* post also carries the
    sound bar's bpm/root/scale_name — and fragment_patch only calls
    _merge_arrangement when the form has a `cell.` key or `bpm`
    (routes/patch.py's fragment_patch). A post of `cutoff` alone never reaches
    _merge_arrangement at all, which would let this test pass even with a
    wrong lane tag. Post *changed* bpm/root/scale_name (distinct from
    whatever the fixture leaves them at) and assert those specific values
    land — proving the "shared" lane is read from the form, not merely
    preserved — while everything else stays exactly as the fixture set
    it, and assert on `s.arrangement`'s identity that the merge path
    really ran."""
    from dataclasses import asdict

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        s = client.app.state.studio
        _fill_non_synth_arrangement(s)
        arr_before = s.arrangement
        before = asdict(s.arrangement)
        # /fragment/patch is the Synths slider route; hx-include="#patch,
        # #sound-bar" means every real post also carries the sound bar's
        # bpm/root/scale_name (sound_bar.html) — without at least "bpm",
        # fragment_patch never calls _merge_arrangement at all. Posted
        # values differ from the arrangement's current ones (bpm=124.0,
        # root="A1", scale_name="pentatonic_minor") to prove they're read
        # from the form rather than coincidentally already matching.
        client.post(
            "/fragment/patch",
            data={"cutoff": "900", "bpm": "140.0", "root": "E2", "scale_name": "major"},
        )
        # proves the merge path actually ran, not just that nothing changed
        assert s.arrangement is not arr_before
        after = asdict(s.arrangement)
        for name in _DRUM_AND_CLIP_FIELDS + _CELL_FIELDS + _GRID_FIELDS:
            assert after[name] == before[name], name
        # the shared fields, by contrast, must take the *posted* values
        assert after["bpm"] == 140.0
        assert after["root"] == "E2"
        assert after["scale_name"] == "major"


def test_perform_grid_post_preserves_drum_and_clip_lanes(tmp_path):
    """The Perform note grid (`#transport`, transport.html) posts to
    `/fragment/arrangement` with a `cell.*` key on every real post — that's
    what makes the cell lane's `cells` change by design (the grid IS the
    post). It must still never disturb the drum machine's own clock/mixer
    or the clip lane, and the grid's own shape/feel (bars/steps/swing/arp)
    must take the *posted* transport values rather than being merely
    preserved or reset. `velocities`/`slides` are cell-lane siblings of
    `cells` but ride their own `vel.`/`slide.` prefixes (routes/patch.py's
    _merge_arrangement) — this post carries neither, so they must be
    preserved, exactly like a real grid click that only toggles a note
    cell leaves the velocity lane and the slide row alone."""
    from dataclasses import asdict

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        s = client.app.state.studio
        _fill_non_synth_arrangement(s)
        arr_before = s.arrangement
        before = asdict(s.arrangement)
        # bars/steps/swing/arp_* are all posted with values distinct from
        # the fixture's (bars=2, steps=8, swing=0.4, arp_mode="up",
        # arp_octaves=2), to prove they're read from the form rather than
        # coincidentally already matching or merely being preserved.
        client.post(
            "/fragment/arrangement",
            data={
                "bars": "4",
                "steps": "32",
                "root": s.arrangement.root,
                "scale_name": s.arrangement.scale_name,
                "swing": "0.55",
                "arp_mode": "down",
                "arp_octaves": "3",
                "cell.A1.0": "on",
            },
        )
        assert s.arrangement is not arr_before  # the merge path really ran
        assert s.arrangement.cells == [["A1", 0]]  # the grid post did land
        after = asdict(s.arrangement)
        for name in _DRUM_AND_CLIP_FIELDS:
            assert after[name] == before[name], name
        # velocities/slides ride their own vel./slide. prefixes, absent
        # from this post, so they must be preserved, not reset
        assert after["velocities"] == before["velocities"]
        assert after["slides"] == before["slides"]
        # the grid's own shape/feel must take the posted values
        assert after["bars"] == 4
        assert after["steps_per_bar"] == 32
        assert after["swing"] == 0.55
        assert after["arp_mode"] == "down"
        assert after["arp_octaves"] == 3


def test_the_grids_own_post_can_empty_the_grid(tmp_path):
    """An unchecked box posts nothing, so the note grid with its last note
    clicked off posts no `cell.*` at all — which read as "this form does not
    carry the grid", and the note stayed: the page showed an empty grid
    while the loop kept playing it (found by tests/browser/test_perform.py).
    The grid's own post carries `span`; with it, absent means off. A note
    the grid cannot show (another key's row) is still kept, and a post
    without `span` (Synths' sound bar: key + bpm) still touches no note."""
    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as client:
        s = client.app.state.studio
        grid = {
            "bars": str(s.arrangement.bars),
            "steps": str(s.arrangement.steps_per_bar),
            "root": s.arrangement.root,
            "scale_name": s.arrangement.scale_name,
            "span": "1",
            "vel.0": "0.9",
        }
        client.post("/fragment/arrangement", data=grid | {"cell.A1.0": "on"})
        client.post("/fragment/arrangement", data=grid | {"slide.0": "on"})
        assert s.arrangement.cells == []  # the second post had no cell.*
        assert s.arrangement.slides == [0]
        client.post("/fragment/arrangement", data=grid)
        assert s.arrangement.slides == []

        s.arrangement.cells = [["A1", 0], ["F#7", 0]]  # F#7: no row in this key
        client.post("/fragment/arrangement", data={"bpm": "120"})
        assert s.arrangement.cells == [["A1", 0], ["F#7", 0]]
        client.post("/fragment/arrangement", data=grid)
        assert s.arrangement.cells == [["F#7", 0]]
