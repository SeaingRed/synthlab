import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_page_has_xypad_and_axis_selects(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="xypad"' in html
    assert 'name="xy_x"' in html
    assert 'name="xy_y"' in html
    assert 'id="xycross"' in html
    # defaults selected
    assert '<option value="cutoff" selected' in html
    assert '<option value="resonance" selected' in html


def test_xy_post_maps_axes_to_extremes(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/xy", data={"x": "1", "y": "0"})
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    html = client.get("/").text
    assert 'value="16000' in html  # cutoff at max (raised 8k -> 16k)
    assert 'value="0.0"' in html  # resonance at min


def test_xy_post_clamps_out_of_range(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/xy", data={"x": "9", "y": "-9"})
    assert r.status_code == 200
    html = client.get("/").text
    assert 'value="16000' in html  # cutoff still clamped to max


def test_xy_axes_update(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/xy/axes", data={"xy_x": "drive", "xy_y": "vibrato_depth"}
    )
    assert r.status_code == 200
    assert 'id="xypad-panel"' in r.text
    assert '<option value="drive" selected' in r.text
    assert '<option value="vibrato_depth" selected' in r.text


def test_xy_axes_rejects_unknown_param(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/xy/axes", data={"xy_x": "nope", "xy_y": "resonance"})
    assert r.status_code == 404


def test_xy_axes_rejects_non_continuous_param(tmp_path):
    client = make_client(tmp_path)
    # osc1_octave is an int field (step == 1.0) -> excluded
    r = client.post(
        "/fragment/xy/axes", data={"xy_x": "osc1_octave", "xy_y": "resonance"}
    )
    assert r.status_code == 404


def test_xypad_has_readout_and_crosshair_lines(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="xy-readout"' in html
    assert 'id="xyline-v"' in html
    assert 'id="xyline-h"' in html
    # axis selects now live inline in the panel's header row alongside the legend
    assert '<div class="xy-head" data-ws-head>' in html


def test_xypad_handles_pointer_cancel_and_capture():
    audio = Path("synthlab/studio/static/audio.js").read_text(encoding="utf-8")
    piano = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert "XY performance pad" not in audio  # block moved out
    xy = piano[piano.index("XY performance pad") :]
    assert "setPointerCapture" in xy
    assert "pointercancel" in xy


def test_xypad_has_play_arm(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="xy-play"' in html
    assert 'class="btn btn-s xy-arm"' in html


def test_piano_js_has_drone_and_freq_helper():
    piano = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert "xy-drone" in piano
    assert "freqOfPitch" in piano


def test_xypad_has_rec_and_clear_arms_and_gesture_data(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="xy-rec"' in html
    assert 'id="xy-clear"' in html
    assert "data-gesture=" in html


def test_piano_js_has_applyxy_and_ghost():
    piano = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert "applyXY" in piano
    assert "xyghost" in piano


def test_xy_gesture_route_accepts_valid_points(tmp_path):
    client = make_client(tmp_path)
    points = json.dumps([[0.0, 0.1, 0.2], [0.5, 0.6, 0.7]])
    r = client.post("/fragment/xy/gesture", data={"points": points})
    assert r.status_code == 200
    html = client.get("/").text
    assert "0.6" in html  # gesture persisted and re-rendered into data-gesture


def test_xy_gesture_route_rejects_too_many_points(tmp_path):
    client = make_client(tmp_path)
    points = json.dumps([[i / 400, 0.5, 0.5] for i in range(301)])
    r = client.post("/fragment/xy/gesture", data={"points": points})
    assert r.status_code == 422


def test_xy_gesture_route_rejects_malformed_points(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/xy/gesture", data={"points": "not json"})
    assert r.status_code == 422
    r2 = client.post(
        "/fragment/xy/gesture", data={"points": json.dumps([[0.0, 1.5, 0.5]])}
    )
    assert r2.status_code == 422


def test_xy_gesture_route_empty_list_clears(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/xy/gesture", data={"points": json.dumps([[0.0, 0.1, 0.2]])})
    r = client.post("/fragment/xy/gesture", data={"points": "[]"})
    assert r.status_code == 200
    html = client.get("/").text
    assert "data-gesture='[]'" in html


def test_xy_gesture_route_rejects_non_finite_values(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/xy/gesture", data={"points": json.dumps([[float("nan"), 0.1, 0.2]])}
    )
    assert r.status_code == 422
    r2 = client.post(
        "/fragment/xy/gesture", data={"points": json.dumps([[0.0, float("inf"), 0.2]])}
    )
    assert r2.status_code == 422


def test_xy_gesture_route_rejects_a_number_too_big_for_a_float(tmp_path):
    """A 400-digit JSON integer parses, and float() of it raises
    OverflowError, not ValueError: it used to be a 500."""
    client = make_client(tmp_path)
    r = client.post("/fragment/xy/gesture", data={"points": f"[[{'9' * 400}, 0, 0]]"})
    assert r.status_code == 422
    assert client.app.state.studio.xy_gesture == []


def test_xy_center_is_an_undo_step(tmp_path):
    """Centring the pad moves two params. It pushed no undo step, so Ctrl+Z
    after it skipped the values it replaced and went back past the edit
    before it as well."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    s._history_at = -1e9  # nothing a moment ago to coalesce with
    s.patch = replace(s.patch, cutoff=300.0, resonance=0.05)
    assert client.post("/fragment/xy/center").status_code == 200
    assert s.patch.cutoff != 300.0
    assert s.pop_history()
    assert (s.patch.cutoff, s.patch.resonance) == (300.0, 0.05)


def test_xypad_loop_toggle_disabled_without_gesture(tmp_path):
    # visible-but-disabled, not hidden: its armed state is sticky
    # (localStorage), so it must always be inspectable
    client = make_client(tmp_path)
    html = client.get("/").text
    seg = html.split('id="xy-loop"')[1][:250]
    assert "disabled" in seg.split(">")[0]
    client.post("/fragment/xy/gesture", data={"points": json.dumps([[0.0, 0.1, 0.2]])})
    seg = client.get("/").text.split('id="xy-loop"')[1][:250]
    assert "disabled" not in seg.split(">")[0]


def test_piano_js_has_loop_toggle_with_persistence():
    piano = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert "xy-loop" in piano
    assert "synthlab.xyloop" in piano


def test_piano_js_has_sync_arms_listener():
    piano = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert "syncArms" in piano
    assert "htmx:after:swap" in piano
