"""GET /fragment/map renders the sound map; POST /fragment/load adopts a point."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.mapview import map_svg
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_empty_map_renders(tmp_path):
    """Nothing auditioned yet: the map is the shipped sounds, faint, and the
    sound on Synths among them (never an empty well)."""
    r = make_client(tmp_path).get("/fragment/map")
    assert r.status_code == 200 and "map-empty" not in r.text
    assert 'class="point active here"' in r.text and "near" in r.text
    # a point is its sound's own Use, by its shelf's route
    for route in (
        "/fragment/library/load",
        "/fragment/recipe/load",
        "/fragment/factory/load",
    ):
        assert f'hx-post="{route}"' in r.text
    # fewer than three rows still says how to fill it
    assert "Audition sounds in Breed to fill the map" in map_svg([])


def test_the_shelf_file_holds_every_shipped_sound(tmp_path):
    """`presets/map_points.json` (tools/refresh_sound_meta.py) lists every
    shipped sound once, measured on the probe; a sample re-measures the same."""
    from synthlab.explore.probe import probe
    from synthlab.studio import mapview

    rows = mapview.shelf_rows()
    names = [r["patch"]["name"] for r in rows]
    shipped = mapview.shelf_sounds()
    assert len(rows) == len(shipped) == len(set(names))
    assert names == [name for _, name, _ in shipped]
    for _source, _name, patch in shipped[::97]:
        got = probe(patch).features.to_dict()
        want = next(r for r in rows if r["patch"]["name"] == _name)["features"]
        for k, v in want.items():
            assert abs(got[k] - v) <= 1e-3 * max(1.0, abs(v)), (_name, k)


def test_map_shows_points_after_breeding(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    html = client.get("/fragment/map").text
    assert html.count("<circle") >= 8 and "<title>" in html


def test_map_has_axes_and_marker(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    html = client.get("/fragment/map").text
    assert "axis-label" in html
    assert "you-are-here" in html or "current" in html


def test_axis_label_font_size_is_caption_scale(tmp_path):
    import re

    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    html = client.get("/fragment/map").text
    sizes = re.findall(r"font-size:([\d.]+)px", html)
    assert sizes, "expected an inline font-size on the axis labels"
    # ~1.5-2% of the viewBox's larger dimension (viewBox width is 100).
    for size in sizes:
        assert 1.0 <= float(size) <= 2.5


def test_load_point_into_editor(tmp_path):
    import re

    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    map_html = client.get("/fragment/map").text
    match = re.search(r'"id": "([a-f0-9]{12})"', map_html)
    assert match is not None
    tid = match.group(1)
    r = client.post("/fragment/load", data={"id": tid})
    assert r.status_code == 200 and 'name="cutoff"' in r.text
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    assert make_client(tmp_path).get("/").status_code == 200  # state persisted cleanly


def test_map_marks_the_actually_loaded_patch_active(tmp_path):
    import re

    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    map_html = client.get("/fragment/map").text
    ids = re.findall(r'"id": "([a-f0-9]{12})"', map_html)
    assert len(ids) >= 2
    tid = ids[0]  # adopt something other than the most-recently-logged row

    client.post("/fragment/load", data={"id": tid})
    html = client.get("/fragment/map").text
    assert f'"id": "{tid}"' in html
    # the adopted point is marked active, not just whatever was logged last
    active_block = re.search(
        r'<a hx-post="/fragment/load" hx-vals=\'\{"id": "'
        + re.escape(tid)
        + r'"\}\'.*?</a>',
        html,
        re.S,
    )
    assert active_block is not None
    assert 'class="point active"' in active_block.group(0)


def test_load_unknown_id_toast(tmp_path):
    r = make_client(tmp_path).post("/fragment/load", data={"id": "ffffffffffff"})
    assert r.status_code == 422 and "toast" in r.text


def test_map_svg_escapes_malicious_patch_name():
    rows = [
        {
            "id": "abcdef012345",
            "x": 0.0,
            "y": 0.0,
            "patch": {"name": "<img src=x onerror=alert(1)>"},
            "features": {"sub_20_60": 0.1, "sub_60_120": 0.1, "centroid": 500.0},
        },
        {
            "id": "abcdef012346",
            "x": 0.5,
            "y": 0.5,
            "patch": {"name": "safe name"},
            "features": {"sub_20_60": 0.1, "sub_60_120": 0.1, "centroid": 500.0},
        },
        {
            "id": "abcdef012347",
            "x": 1.0,
            "y": 1.0,
            "patch": {"name": "another safe"},
            "features": {"sub_20_60": 0.1, "sub_60_120": 0.1, "centroid": 500.0},
        },
    ]
    svg = map_svg(rows)
    assert "<img" not in svg
    assert "&lt;img" in svg


def test_up_is_more_on_the_y_axis():
    """The rotated y label reads its low word at the bottom, so the point
    with the highest y value is drawn nearest the top (smallest SVG y)."""
    import re

    from synthlab.studio.mapview import map_svg

    ids = [f"{i:012x}" for i in range(5)]
    rows = [{"id": k, "x": float(i), "y": float(i)} for i, k in enumerate(ids)]
    svg = map_svg(rows)
    found = re.findall(r'"id": "([0-9a-f]{12})".*?cy="([\d.]+)"', svg)
    cy = {k: float(v) for k, v in found}
    assert len(cy) == 5 and cy[ids[4]] < cy[ids[0]]
