"""Pitch bend in the studio: the keyboard's wheel (strip, MIDI, XY axis) is
a performance, the bend range and a recorded bend are the sound's."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio.paramui import CONTINUOUS_PARAMS, MOD_TARGET_LABELS, spec_of
from synthlab.studio.server import create_app

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/"}
PIANO = Path("synthlab/studio/static/piano.js")


def client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _patch(c: TestClient) -> dict:
    return c.get("/patch.json").json()


def test_the_keyboard_carries_a_bend_strip_range_menu_and_record(tmp_path):
    html = client(tmp_path).get("/").text
    strip = re.search(r'<div id="bend-strip"[^>]*>', html).group(0)
    for attr in ('role="slider"', 'tabindex="0"', "aria-valuenow=", "aria-valuemin="):
        assert attr in strip
    assert 'id="bend-range"' in html and 'id="bend-rec"' in html
    for r in (2, 7, 12, 24):
        assert f'<option value="{r}">±{r}</option>' in html


def test_bend_range_and_pitch_bend_are_panel_rows_macros_and_xy_can_reach():
    assert spec_of("bend_range").max == 24.0 and spec_of("bend_range").unit == "st"
    assert (spec_of("pitch_bend").min, spec_of("pitch_bend").max) == (-1.0, 1.0)
    assert {"bend_range", "pitch_bend", "mod1_amt"} <= set(CONTINUOUS_PARAMS)
    assert "pitch" in [v for v, _ in spec_of("mod1_dst").options]
    assert MOD_TARGET_LABELS["pitch"].startswith("Pitch")


def test_the_range_menu_sets_the_sounds_bend_range(tmp_path):
    c = client(tmp_path)
    r = c.post("/fragment/bend-range", data={"value": "12"}, headers=HX)
    assert r.status_code == 200
    assert 'id="p-bend_range"' in r.text  # its panel row, OOB
    assert _patch(c)["bend_range"] == 12.0
    c.post("/fragment/bend-range", data={"value": "99"}, headers=HX)
    assert _patch(c)["bend_range"] == 24.0  # the cap
    assert c.post("/fragment/bend-range", data={"value": "x"}).status_code == 422


def test_the_xy_pitch_bend_axis_is_a_performance_not_a_patch_edit(tmp_path):
    c = client(tmp_path)
    c.post("/fragment/xy/axes", data={"xy_x": "pitch_bend", "xy_y": "cutoff"})
    c.post("/fragment/xy", data={"x": "1", "y": "1"}, headers=HX)
    p = _patch(c)
    assert p["pitch_bend"] == 0.0 and round(p["cutoff"]) == 16000
    c.post("/fragment/xy/axes", data={"xy_x": "pitch_bend", "xy_y": "pitch_bend"})
    assert c.post("/fragment/xy", data={"x": "0", "y": "1"}).status_code == 204
    assert _patch(c)["pitch_bend"] == 0.0
    # piano.js: the axis drives the wheel, and springs back on release
    js = PIANO.read_text(encoding="utf-8")
    assert 'if (param === "pitch_bend") { setWheel(value); continue; }' in js


def test_a_macro_never_writes_pitch_bend_into_the_patch(tmp_path):
    """The XY pad refused the performance controls; a macro did not, so a
    knob could leave `pitch_bend` = 1.0 in the patch — a static bend that
    transposes the loop."""
    from synthlab.studio import schema
    from synthlab.studio.paramui import MACRO_PARAMS
    from synthlab.studio.routes.macros import PERFORMANCE_AXES, apply_macro

    assert "pitch_bend" in PERFORMANCE_AXES
    assert set(MACRO_PARAMS) == set(CONTINUOUS_PARAMS) - PERFORMANCE_AXES
    assert "bend_range" in MACRO_PARAMS  # the sound's own setting, as on the pad
    c = client(tmp_path)
    s = c.app.state.studio
    # the menu does not offer it, and an assignment drops it
    menu = re.search(r'<select name="param".*?</select>', c.get("/").text, re.S)
    assert 'value="pitch_bend"' not in menu.group(0)
    assert 'value="bend_range"' in menu.group(0)
    r = c.post(
        "/fragment/macro/0/assign",
        data={"param": ["pitch_bend", "cutoff"], "amt": ["1", "1"]},
        headers=HX,
    )
    assert r.status_code == 200
    assert [t["param"] for t in s.macros[0]["targets"]] == ["cutoff"]
    # one that got in anyway (an older session's) moves nothing
    s.macros[1] = {
        "name": "Bend",
        "value": 0.0,
        "targets": [{"param": "pitch_bend", "amt": 1.0}],
    }
    assert (
        c.post("/fragment/macro/1", data={"value": "1"}, headers=HX).status_code == 200
    )
    assert _patch(c)["pitch_bend"] == 0.0
    s.macros[1]["value"] = 1.0
    assert apply_macro(s, 1) == [] and s.patch.pitch_bend == 0.0
    # and the schema drops it on load: a saved macro is ignored, not a crash
    saved = [
        {
            "name": "Bend",
            "value": 1.0,
            "targets": [
                {"param": "pitch_bend", "amt": 1.0},
                {"param": "cutoff", "amt": 0.5},
            ],
        }
    ]
    clean = schema.conform(saved, schema.nodes().MACROS)
    assert clean[0]["targets"] == [{"param": "cutoff", "amt": 0.5}]
    assert clean[0]["name"] == "Bend" and len(clean) == 4


def test_record_bend_lands_as_the_pitch_bend_curve(tmp_path):
    c = client(tmp_path)
    pts = [[0, 0.5], [0.25, 1.0], [0.5, 0.5], [1, 0.5]]
    r = c.post(
        "/fragment/curve/pitch_bend/set", data={"points": json.dumps(pts)}, headers=HX
    )
    assert r.status_code == 200
    state = c.get("/project.json").json()
    assert state["curves"]["pitch_bend"] == pts
    bad = {"points": "[[0, 2], [1, 0.5]]"}  # clamped to 0..1 by the schema
    assert c.post("/fragment/curve/pitch_bend/set", data=bad).status_code == 200
    assert (
        c.post("/fragment/curve/pitch_bend/set", data={"points": "{"}).status_code
        == 422
    )
    assert c.post("/fragment/curve/nope/set", data={"points": "[]"}).status_code == 404


def test_the_wheel_reaches_every_voice_and_every_wav_key():
    """One path for the strip, MIDI and the XY axis: a "wheel" message in
    semitones (livefx.js forwards any message without a pitch to every
    lane) and keys.js's playbackRate bend."""
    js = PIANO.read_text(encoding="utf-8")
    assert 'postMessage({ type: "wheel", st })' in js
    assert "window.synthlab.bendKeys?.(st)" in js
    midi = Path("synthlab/studio/static/midi.js").read_text(encoding="utf-8")
    assert "p.wheel(((d2 << 7 | d1) - 8192) / 8192)" in midi
    worklet = Path("synthlab/studio/static/voice-worklet.js").read_text(
        encoding="utf-8"
    )
    assert 'm.type === "wheel"' in worklet


# ---- the arrow bend: hold up/down to glide the wheel, let go to come back
# (`node -e`: the script's own arguments start at argv[1])

KEYS = Path("synthlab/studio/static/keys.js")
STEP_HARNESS = """
globalThis.window = { synthlab: {} };
globalThis.document = { addEventListener() {} };
eval(require("fs").readFileSync(process.argv[1], "utf8"));
const { dir, secs, curve, from, dt, frames } = JSON.parse(process.argv[2]);
let w = from, t = 0;
const out = [];
for (let i = 0; i < frames; i++) {
  t += dt;
  w = window.synthlab.bendStep(w, dir, t, dt, secs, curve);
  out.push(w);
}
process.stdout.write(JSON.stringify(out));
"""


def _glide(**spec) -> list[float]:
    """keys.js's own bendStep, frame by frame under node (60 fps)."""
    if shutil.which("node") is None:
        pytest.skip("node is needed to run keys.js's own code")
    spec = {"from": 0.0, "dt": 1 / 60, **spec}
    out = subprocess.run(
        ["node", "-e", STEP_HARNESS, str(KEYS), json.dumps(spec)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def test_holding_an_arrow_reaches_the_top_in_the_rise_time():
    # 0.3 s at 60 fps is 18 frames: steady, it is exactly halfway at 9
    w = _glide(dir=1, secs=0.3, curve="linear", frames=24)
    assert w[8] == pytest.approx(0.5)
    assert w[16] < 1 and w[17] == pytest.approx(1) and w[-1] == 1  # and stays
    down = _glide(dir=-1, secs=0.3, curve="linear", frames=24)
    assert down[17] == pytest.approx(-1)


def test_ease_starts_slow_and_arrives_on_time():
    lin = _glide(dir=1, secs=0.6, curve="linear", frames=40)
    ease = _glide(dir=1, secs=0.6, curve="ease", frames=40)
    assert ease[8] < lin[8] / 2  # a quarter of the way in: well behind
    assert ease[35] == pytest.approx(1)  # 36 frames = 0.6 s: there on time
    steps = [b - a for a, b in zip(ease[:30], ease[1:31], strict=True)]
    assert all(b > a for a, b in zip(steps, steps[1:], strict=False))


def test_letting_go_glides_home_over_the_release_time():
    w = _glide(dir=0, secs=0.2, curve="linear", frames=14, **{"from": 1.0})
    assert w[5] == pytest.approx(0.5) and w[11] == pytest.approx(0) and w[-1] == 0
    # from partway (let go before the top): the same speed, home sooner
    part = _glide(dir=0, secs=0.2, curve="linear", frames=8, **{"from": 0.25})
    assert part[2] == pytest.approx(0)


def test_a_zero_time_jumps_straight_there():
    assert _glide(dir=1, secs=0, curve="linear", frames=1) == [1]
    assert _glide(dir=0, secs=0, curve="ease", frames=1, **{"from": -1.0}) == [0]


def test_the_dock_carries_the_arrow_bend_settings(tmp_path):
    html = client(tmp_path).get("/").text
    for el in ("bend-arrows", "bend-rise", "bend-release", "bend-curve"):
        assert f'id="{el}"' in html
    assert '<option value="always">' in html and '<option value="strip">' in html
    assert '<option value="linear">' in html and '<option value="ease">' in html
    assert re.search(r'id="bend-rise"[^>]*value="300"', html)
    assert re.search(r'id="bend-release"[^>]*value="200"', html)
    js = PIANO.read_text(encoding="utf-8")
    assert "window.synthlab.bendStep(" in js and "setWheel(w)" in js
