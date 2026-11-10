from synthlab.studio.curves import CURVABLE, curve_array, curve_svg, toggle_point
from synthlab.studio.render import RenderInputs, render_loop
from synthlab.studio.state import Arrangement
from synthlab.synth import preset


def test_toggle_add_sort_remove():
    pts = toggle_point([], 0.5, 0.5)
    pts = toggle_point(pts, 0.1, 0.9)
    assert pts == [[0.1, 0.9], [0.5, 0.5]]
    pts = toggle_point(pts, 0.51, 0.1)  # within 0.04 of 0.5: removes
    assert pts == [[0.1, 0.9]]


def test_curve_array_maps_range():
    arr = curve_array([[0.0, 0.0], [1.0, 1.0]], "cutoff", 100)
    assert arr is not None
    assert arr[0] == 60.0 and abs(arr[-1] - 16000.0) < 1e-6
    assert curve_array([[0.5, 0.5]], "cutoff", 10) is None


def test_cutoff_curve_reaches_the_whole_slider_range():
    """The curve was capped at 8 kHz while the slider and the breeder both
    run to 16 kHz, so the top half of the knob was undrawable."""
    from synthlab.params import MOD_RANGES, PARAMS

    assert CURVABLE["cutoff"] == PARAMS["cutoff"].gene
    # the mod matrix's cutoff depth is a different scale and stays at 8 kHz
    assert MOD_RANGES["cutoff"] == (60.0, 8000.0)


def test_curved_render_differs_and_keys_differ():
    a = Arrangement(cells=[["A1", 0]])
    p = preset("bass_reese")
    plain = render_loop(RenderInputs(patch=p, arr=a))
    curved_inp = RenderInputs(
        patch=p, arr=a, curves={"cutoff": [[0.0, 0.0], [1.0, 1.0]]}
    )
    curved = render_loop(curved_inp)
    assert plain.key != curved.key
    assert plain.wav != curved.wav
    assert curved_inp.key() == curved.key


def test_curve_position_reaches_notes():
    a = Arrangement(cells=[["A1", 2]])
    p = preset("bass_reese")
    up = render_loop(
        RenderInputs(patch=p, arr=a, curves={"cutoff": [[0.0, 0.0], [1.0, 1.0]]})
    )
    down = render_loop(
        RenderInputs(patch=p, arr=a, curves={"cutoff": [[0.0, 1.0], [1.0, 0.0]]})
    )
    assert up.wav != down.wav


def test_unknown_curve_param_ignored():
    a = Arrangement(cells=[["A1", 0]])
    res = render_loop(
        RenderInputs(
            patch=preset("bass_reese"), arr=a, curves={"unison": [[0, 0], [1, 1]]}
        )
    )
    assert res.wav  # int fields are not curvable; silently skipped


def test_svg_contains_points():
    svg = curve_svg([[0.0, 0.2], [1.0, 0.8]], "cutoff")
    assert svg.startswith("<svg") and "circle" in svg and 'data-param="cutoff"' in svg


def test_curve_route_roundtrip(tmp_path):
    from tests.studio.test_routes import make_client

    client = make_client(tmp_path)
    r = client.get("/fragment/curve/cutoff")
    assert r.status_code == 200 and 'data-param="cutoff"' in r.text
    r = client.post(
        "/fragment/curve/cutoff", data={"x": "10", "y": "5", "w": "100", "h": "40"}
    )
    assert r.status_code == 200 and "circle" in r.text
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    r = client.post("/fragment/curve/cutoff/clear")
    assert "circle" not in r.text


def test_curve_unknown_param_404(tmp_path):
    from tests.studio.test_routes import make_client

    assert make_client(tmp_path).get("/fragment/curve/unison").status_code == 404


def test_editor_page_has_curve_toggles(tmp_path):
    from tests.studio.test_routes import make_client

    html = make_client(tmp_path).get("/").text
    assert "/fragment/curve/cutoff" in html
    # the curve opens under its own row, not in a shared editor
    row = html.split('id="row-cutoff"')[1].split('id="row-')[0]
    assert 'hx-target="next .curve-slot"' in row and 'class="curve-slot"' in row
    assert 'id="curve-editor"' not in html


def test_inline_curve_targets_its_slot_and_closes(tmp_path):
    from tests.studio.test_routes import make_client

    client = make_client(tmp_path)
    box = client.get("/fragment/curve/cutoff").text
    assert box.count('hx-target="closest .curve-slot"') >= 3  # click, clear, add
    assert "Cutoff over each note" in box
    assert client.get("/fragment/curve-close").text == ""
