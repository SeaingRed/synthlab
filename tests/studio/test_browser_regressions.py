"""Server-side failures found by driving the studio in a browser (Sep 2026).

Each test is the browser's failing gesture, replayed through the routes:
morph left the panels stale, hx-prompt names arrived URL-encoded, a project
import never reloaded the page, the Bass check's sub spectrum was a flat
line at the ceiling, "→ grid" wiped the pattern and landed nothing, Ctrl+Z
after a reset skipped the pre-reset sound, and the chord bar kept the old
key.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.analysis.bass import sub_spectrum
from synthlab.core import SR
from synthlab.studio.bassview import sub_spectrum_svg
from synthlab.studio.clips import list_clips
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState, pitch_rows
from synthlab.synth import Patch


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _hx(view: str = "editor", **extra) -> dict:
    return {
        "HX-Request": "true",
        "HX-Current-URL": f"http://testserver/view/{view}",
        **extra,
    }


def _st(client) -> StudioState:
    return client.app.state.studio


def _oob_tag(html: str, el_id: str) -> str | None:
    """The opening tag of the element with `el_id`, if it is sent OOB."""
    for tag in re.findall(r"<[a-z]+\b[^>]*>", html):
        if f'id="{el_id}"' in tag and "hx-swap-oob" in tag:
            return tag
    return None


# --- 1. morph answers with the rows it moved and a reset slider -----------------


def test_morph_sends_back_the_moved_rows_and_springs_the_slider(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    s.patch = replace(s.patch, cutoff=500.0, attack=0.01)
    s.slot_a = replace(s.patch, name="bright", cutoff=6000.0, attack=0.4)
    r = client.post("/fragment/morph", data={"slot": "a", "t": "0.5"}, headers=_hx())
    assert r.status_code == 200
    for name in ("cutoff", "attack"):
        tag = _oob_tag(r.text, f"row-{name}")
        assert tag is not None, f"row-{name} not sent back"
    # the row carries the blended value, not the one it was drawn with
    row = r.text.split('id="row-attack"', 1)[1].split("</div>", 1)[0]
    assert f'value="{s.patch.attack}"' in row
    # rows the blend left alone stay put
    assert _oob_tag(r.text, "row-release") is None
    # the A/B strip comes back on its own, its morph slider at 0 again
    ab = _oob_tag(r.text, "ab-slots")
    assert ab is not None and 'hx-swap-oob="outerHTML"' in ab
    strip = r.text.split('id="ab-slots"', 1)[1]
    assert re.search(r'name="t"', strip) and 'value="0" name="t"' in strip


# --- 2. hx-prompt values are decoded --------------------------------------------


def test_prompted_names_arrive_decoded(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    client.post("/fragment/preset/save", data={"name": "wt"}, headers=_hx())
    # the official extension sends encodeURI(value)
    r = client.post(
        "/fragment/preset/rename",
        data={"name": "wt"},
        headers=_hx(**{"HX-Prompt": "wt%20renamed"}),
    )
    assert r.status_code == 200
    assert "wt renamed" in s.list_presets()
    assert not any("_20" in p for p in s.list_presets())

    client.post(
        "/clip/bounce", headers=_hx(**{"HX-Prompt": "wt%20b2%20%C3%A9"}), data={}
    )
    names = [c["name"] for c in list_clips(s.data_dir)]
    assert any(n.startswith("wt b2") for n in names), names
    assert not any("_20" in n or "_C3" in n for n in names), names

    client.post(
        "/fragment/panel/preset/save",
        data={"group": "Filter"},
        headers=_hx(**{"HX-Prompt": "dark%20lp"}),
    )
    assert "dark lp" in s.list_panel_presets("Filter")


# --- 3. a project import reloads the page ---------------------------------------


def test_htmx_import_answers_with_a_refresh_not_a_redirect(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    payload = s.project_payload()
    payload["arrangement"]["bpm"] = 97.0
    r = client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(payload), "application/json")},
        headers=_hx(),
        follow_redirects=False,
    )
    # fetch() follows a 303 by itself, so htmx never saw HX-Redirect
    assert r.status_code == 200, r.status_code
    assert r.headers.get("HX-Refresh") == "true"
    assert "location" not in {k.lower() for k in r.headers}
    assert s.arrangement.bpm == 97.0
    bad = client.post(
        "/project/import",
        files={"file": ("p.json", b"{nope", "application/json")},
        headers=_hx(),
    )
    assert bad.status_code == 422 and "toast" in bad.text
    assert "HX-Refresh" not in bad.headers


# --- 4. the sub spectrum has a shape --------------------------------------------


def _points(svg: str) -> list[tuple[float, float]]:
    """The curve's points (`sigdraw.smooth_path`: each value a control
    point, the curve through the midpoints), less its closing outline."""
    assert 'class="sub-spectrum sig"' in svg  # THE signal graphic
    d = re.search(r'<path d="([^"]+)"', svg).group(1)
    body = d.split("Q", 1)[1].rsplit("L", 1)[0]
    nums = [float(n) for n in re.findall(r"-?(?:\d+\.?\d*|\.\d+)", body)]
    return list(zip(nums[::2], nums[1::2], strict=True))


def test_sub_spectrum_reads_dbfs_and_the_chart_shows_the_peak():
    t = np.arange(2 * SR) / SR
    tone = 0.3 * np.sin(2 * np.pi * 55.0 * t)
    spec = sub_spectrum(tone)
    freqs = np.array([f for f, _ in spec])
    db = np.array([d for _, d in spec])
    peak = int(np.argmax(db))
    assert abs(freqs[peak] - 55.0) < 1.0
    # a 0.3 sine is 20*log10(0.3) = -10.5 dBFS (it used to read +76 dB)
    assert abs(db[peak] - 20 * np.log10(0.3)) < 0.5

    pts = _points(sub_spectrum_svg(np.column_stack([tone, tone])))
    xs = np.array([x for x, _ in pts])
    ys = np.array([y for _, y in pts])
    top = int(np.argmin(ys))  # louder = higher = smaller y
    assert abs(xs[top] - (55.0 - 10.0) / 110.0 * 100.0) < 1.5
    assert 0.0 < ys[top] < 40.0, "the peak sits below the 0 dB ceiling"
    assert np.ptp(ys) > 10.0, "the chart has a shape, not a flat line"
    assert np.median(ys) > ys[top] + 10.0

    silent = _points(sub_spectrum_svg(np.zeros((2 * SR, 2))))
    assert all(y == 40.0 for _, y in silent), "silence draws the floor"


# --- 5. "→ grid" folds chords onto the grid's rows ------------------------------


def _key(client, root: str, scale: str) -> None:
    s = _st(client)
    s.arrangement = replace(s.arrangement, root=root, scale_name=scale)


def test_chords_to_grid_fold_into_the_rows(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    _key(client, "A1", "pentatonic_minor")
    # the browser's case: two notes in a 1-bar grid, plus one hidden past it
    s.arrangement = replace(
        s.arrangement, bars=1, steps_per_bar=16, cells=[["A1", 3], ["C2", 5]]
    )
    rows = pitch_rows(s.arrangement)  # A1 C2 D2 E2 G2 A2
    r = client.post("/fragment/chords/grid", data={"code": "Am F C G"}, headers=_hx())
    assert r.status_code == 200
    cells = {(p, int(st)) for p, st in s.arrangement.cells}
    assert cells, "the chords landed nothing"
    assert {p for p, _ in cells} <= set(rows)
    # the sequence replaces what the grid showed: one chord per step
    assert {st for _, st in cells} == {0, 1, 2, 3}
    # Am (A3 C4 E4) folds down onto the A, C and E rows
    assert {p for p, st in cells if st == 0} == {"A2", "C2", "E2"}
    # F's F is outside A pentatonic minor: it moves to a neighbour, and says so
    assert {p for p, st in cells if st == 1} == {"E2", "A2", "C2"}
    toast = json.loads(r.headers["HX-Trigger"]).get("studio-toast", {})
    assert "nearest grid row" in toast.get("message", "")
    # a note already on a row stays on it (not folded an octave down)
    client.post("/fragment/chords/grid", data={"code": "A1 A2"}, headers=_hx())
    assert sorted(map(tuple, s.arrangement.cells)) == [("A1", 0), ("A2", 1)]


def test_chords_to_grid_keeps_notes_the_grid_hides(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    _key(client, "A1", "pentatonic_minor")
    s.arrangement = replace(
        s.arrangement, bars=1, steps_per_bar=4, cells=[["A1", 3], ["C2", 5]]
    )
    client.post("/fragment/chords/grid", data={"code": "Am"}, headers=_hx())
    assert ["C2", 5] in [list(c) for c in s.arrangement.cells]  # past the grid
    assert ["A1", 3] not in [list(c) for c in s.arrangement.cells]


def test_chords_to_grid_never_clears_the_grid_for_nothing(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    _key(client, "A1", "pentatonic_minor")
    s.arrangement = replace(s.arrangement, cells=[["A1", 3], ["C2", 5]])
    r = client.post("/fragment/chords/grid", data={"code": "~ ~"}, headers=_hx())
    assert r.status_code == 422 and "toast" in r.text
    assert [list(c) for c in s.arrangement.cells] == [["A1", 3], ["C2", 5]]


# --- 6. a reset is one undo step ------------------------------------------------


def test_undo_after_a_reset_brings_back_the_sound_before_it(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    for mode in ("defaults", "revert"):
        s.patch = Patch(name="mine", cutoff=321.0)
        s.mark_baseline("sound", "saved")
        client.post("/fragment/patch", data={"cutoff": "777"}, headers=_hx())
        assert s.patch.cutoff == 777.0
        r = client.post(
            "/fragment/reset", data={"scope": "sound", "mode": mode}, headers=_hx()
        )
        assert r.status_code == 200
        assert s.patch.cutoff != 777.0
        client.post("/fragment/undo", headers=_hx())
        assert s.patch.cutoff == 777.0, f"undo after {mode} skipped the edit"


def test_a_reset_that_changes_nothing_adds_no_undo_step(tmp_path):
    client = _client(tmp_path)
    s = _st(client)
    depth = len(s._history)
    s.mark_baseline("sound", "saved")
    client.post("/fragment/reset", data={"scope": "sound", "mode": "revert"})
    assert len(s._history) == depth


# --- 7. the chord bar follows the key -------------------------------------------


def test_key_change_re_sends_the_chord_bar(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/fragment/arrangement", data={"root": "E2", "bpm": "120"}, headers=_hx()
    )
    tag = _oob_tag(r.text, "chord-bar")
    assert tag is not None, "a key change left the chord bar on the old key"
    # morphed, so the code line and the pad blocks survive
    assert 'hx-swap-oob="outerMorph"' in tag
    bar = r.text.split('id="chord-bar"', 1)[1]
    assert "/fragment/chordlib?key=E2-" in bar  # the new key's chord chart
    for block in ("pads-notes", "pads-chords", "pads-harmonics", "chord-lib"):
        assert f'id="{block}"' in bar
    # a tempo nudge leaves it alone
    r = client.post(
        "/fragment/arrangement", data={"root": "E2", "bpm": "121"}, headers=_hx()
    )
    assert _oob_tag(r.text, "chord-bar") is None


def test_renaming_the_loaded_sound_renames_it_everywhere(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("Warm Lead")
    s.patch = replace(s.patch, cutoff=900.0)  # an unsaved edit
    assert s.rename_preset("Warm Lead", "Cold Lead")
    assert s.patch.name == "Cold Lead"
    assert s.is_dirty("sound")  # the edit is still unsaved
    s.load_preset("Cold Lead")
    assert s.patch.name == "Cold Lead"


def test_attack_defaults_are_reachable_slider_positions():
    from synthlab.studio.paramui import spec_of

    for name in ("attack", "filter_attack"):
        spec = spec_of(name)
        steps = (spec.default - spec.min) / spec.step
        assert abs(steps - round(steps)) < 1e-9, name


def test_jam_resumes_a_suspended_context():
    from pathlib import Path

    js = Path("synthlab/studio/static/audio.js").read_text(encoding="utf-8")
    jam = js.split('closest("#jam-rec")')[1][:300]
    assert "ctx.resume()" in jam


def test_phone_monitor_rule_comes_after_the_desktop_one():
    from pathlib import Path

    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    desktop = css.index(".monitor label { display: grid;")
    phone = css.index("@media (max-width: 640px) { .monitor label")
    assert phone > desktop
