from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_panel_reset_only_touches_its_group(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/patch", data={"cutoff": "500", "attack": "0.9"})
    r = client.post("/fragment/panel/reset", data={"group": "Filter"})
    assert r.status_code == 200
    html = r.text
    assert 'value="2200' in html  # cutoff back to Patch() default
    assert 'value="0.9"' in html  # Envelope attack untouched
    assert "preview-ready" in r.headers.get("HX-Trigger", "")


def test_panel_preset_roundtrip(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/patch", data={"cutoff": "777"})
    r = client.post(
        "/fragment/panel/preset/save", data={"group": "Filter", "name": "gritty"}
    )
    assert "gritty" in r.text
    client.post("/fragment/patch", data={"cutoff": "3000"})
    r = client.post(
        "/fragment/panel/preset/load", data={"group": "Filter", "name": "gritty"}
    )
    assert 'value="777' in r.text
    assert "preview-ready" in r.headers.get("HX-Trigger", "")


def test_unknown_group_404(tmp_path):
    assert (
        make_client(tmp_path)
        .post("/fragment/panel/reset", data={"group": "Nope"})
        .status_code
        == 404
    )


def test_panel_dice_touches_only_that_panels_fields(tmp_path):
    """Per-panel randomize: randomize just this panel while the
    oscillators stay put — mutate() filtered to the panel's field list."""
    from dataclasses import asdict

    from synthlab.studio.paramui import PARAM_GROUPS

    with make_client(tmp_path) as client:
        s = client.app.state.studio
        before = asdict(s.patch)
        r = client.post("/fragment/panel/dice", data={"group": "Filter"})
        assert r.status_code == 200
        after = asdict(s.patch)
        filter_fields = {
            sp.name for g, specs in PARAM_GROUPS for sp in specs if g == "Filter"
        }
        changed = {k for k in before if before[k] != after[k] and k != "name"}
        assert changed, "dice must change something"
        assert changed <= filter_fields, changed - filter_fields


def test_panel_dice_that_changes_nothing_adds_no_undo_step(tmp_path, monkeypatch):
    """Five draws that all leave the panel alone must not leave an empty
    undo step behind (Ctrl+Z would then appear to do nothing)."""
    import synthlab.explore.breed as breed

    monkeypatch.setattr(breed, "mutate", lambda patch, amount: (patch, []))
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        depth = len(s._history)
        r = client.post("/fragment/panel/dice", data={"group": "Filter"})
        assert r.status_code == 200
        assert len(s._history) == depth


def test_panel_dice_unknown_group_is_404(tmp_path):
    assert (
        make_client(tmp_path)
        .post("/fragment/panel/dice", data={"group": "Nope"})
        .status_code
        == 404
    )
