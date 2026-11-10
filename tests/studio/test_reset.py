"""Every page's undo net: revert a scope to how it was when last loaded or
saved, or reset it to factory defaults."""

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app
from synthlab.studio.state import (
    RESET_SCOPES,
    RESET_VIEWS,
    Arrangement,
    StudioState,
)
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://x/view/{view}"}


def test_snapshot_revert_and_defaults_round_trip(tmp_path):
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=1234.0, name="Mine")
    s.mark_baseline("sound", "Mine")
    assert not s.is_dirty("sound")

    s.patch = replace(s.patch, cutoff=99.0)
    assert s.is_dirty("sound")
    assert s.revert("sound") and s.patch.cutoff == 1234.0
    assert not s.is_dirty("sound")

    # defaults wipe it AND rebaseline, so revert can't silently undo them
    assert s.reset_defaults("sound")
    assert s.patch.cutoff == Patch().cutoff and s.baseline_label("sound") == "defaults"
    assert not s.is_dirty("sound")

    # a scope whose baseline is missing can't be reverted (and isn't "dirty")
    s.baselines.pop("sound")
    assert s.revert("sound") is False
    assert s.is_dirty("sound") is False
    assert s.revert("nope") is False and s.reset_defaults("nope") is False


def test_scopes_are_disjoint_and_cover_the_views():
    seen: set[str] = set()
    for _, keys in RESET_SCOPES.values():
        assert not (seen & set(keys)), "a key belongs to exactly one scope"
        seen |= set(keys)
    for view, scopes in RESET_VIEWS.items():
        assert scopes, view
        assert set(scopes) <= set(RESET_SCOPES), view


def test_revert_only_touches_its_own_scope(tmp_path):
    s = StudioState(tmp_path)
    s.mark_baselines(RESET_SCOPES, "start")
    s.patch = replace(s.patch, cutoff=300.0)
    s.arrangement = replace(
        s.arrangement, drums=[["kick", 0]], drum_bars=4, cells=[["A1", 3]]
    )
    s.revert("pattern")
    assert s.arrangement.drums == [] and s.arrangement.drum_bars == 1
    assert s.patch.cutoff == 300.0  # sound untouched
    assert s.arrangement.cells == [["A1", 3]]  # sequence untouched


def test_loading_a_preset_or_pattern_sets_the_baseline(tmp_path):
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=777.0)
    s.save_preset("Mine")
    assert s.baseline_label("sound") == "Mine"
    s.patch = replace(s.patch, cutoff=100.0)
    assert s.is_dirty("sound")
    s.revert("sound")
    assert s.patch.cutoff == 777.0

    s.bank_load(0)  # a factory beat
    assert s.baseline_label("pattern") == s.drum_bank[0]["name"]
    assert not s.is_dirty("pattern")
    s.arrangement = replace(s.arrangement, drums=[])
    assert s.is_dirty("pattern")
    assert s.revert("pattern") and s.arrangement.drums == s.drum_bank[0]["steps"]


def test_baselines_survive_a_restart(tmp_path):
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=640.0)
    s.save_preset("Keeper")
    s.patch = replace(s.patch, cutoff=120.0)
    s.save()
    again = StudioState(tmp_path)
    assert again.baseline_label("sound") == "Keeper"
    assert again.revert("sound") and again.patch.cutoff == 640.0


def test_reset_menu_on_every_view(tmp_path):
    client = make_client(tmp_path)
    for view in RESET_VIEWS:
        html = client.get(f"/view/{view}").text
        assert 'class="reset-menu"' in html, view
        assert 'hx-vals=\'{"scope": "all", "mode": "defaults"}\'' in html, view
        for scope in RESET_VIEWS[view]:
            assert f'"scope": "{scope}"' in html, (view, scope)


def test_reset_route_stays_on_its_view_and_guards_scope(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(s.arrangement, drum_humanize=0.5, drums=[["kick", 0]])
    s.mark_baseline("pattern", "before")
    s.arrangement = replace(s.arrangement, drum_humanize=0.0, drums=[])

    r = client.post(
        "/fragment/reset",
        data={"scope": "pattern", "mode": "revert"},
        headers=_hx("drums"),
    )
    assert r.status_code == 200
    assert 'data-view="drums"' in r.text  # never bounced onto the editor
    assert s.arrangement.drums == [["kick", 0]] and s.arrangement.drum_humanize == 0.5

    # a scope the view doesn't own is refused
    assert (
        client.post(
            "/fragment/reset",
            data={"scope": "board", "mode": "revert"},
            headers=_hx("drums"),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/fragment/reset",
            data={"scope": "pattern", "mode": "nope"},
            headers=_hx("drums"),
        ).status_code
        == 422
    )


def test_reset_all_defaults_for_a_view(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.patch = replace(s.patch, cutoff=555.0)
    s.arrangement = replace(s.arrangement, cells=[["A1", 7]], bars=3)
    s.fx_master = [{"kind": "drive", "params": {}}]
    r = client.post(
        "/fragment/reset",
        data={"scope": "all", "mode": "defaults"},
        headers=_hx("editor"),
    )
    assert r.status_code == 200 and 'data-view="editor"' in r.text
    assert s.patch.cutoff == Patch().cutoff
    assert s.fx_master == []
    # the note grid moved to Perform, so a Synths reset must leave it alone —
    # resetting the sound must never cost you the pattern you were writing
    assert s.arrangement.cells == [["A1", 7]] and s.arrangement.bars == 3
    assert "sequence" not in RESET_VIEWS["editor"]
    # ...and Perform, which owns it now, does reset it
    r = client.post(
        "/fragment/reset",
        data={"scope": "sequence", "mode": "defaults"},
        headers=_hx("perform"),
    )
    assert r.status_code == 200
    assert s.arrangement.cells == Arrangement().cells and s.arrangement.bars == 1
    # the drum machine is not the editor's business
    assert "kit" not in RESET_VIEWS["editor"]


def test_revert_with_no_baseline_toasts(tmp_path):
    client = make_client(tmp_path)
    client.app.state.studio.baselines = {}  # nothing to go back to
    r = client.post(
        "/fragment/reset",
        data={"scope": "board", "mode": "revert"},
        headers=_hx("breed"),
    )
    assert r.status_code == 422 and "toast" in r.text
    # defaults never needs a baseline
    assert (
        client.post(
            "/fragment/reset",
            data={"scope": "board", "mode": "defaults"},
            headers=_hx("breed"),
        ).status_code
        == 200
    )


def test_a_session_without_baselines_baselines_itself(tmp_path):
    """Sessions saved before this feature (and brand-new ones) must still
    offer Revert — 'how it was' is how the session opened."""
    fresh = StudioState(tmp_path)
    assert fresh.baseline_label("sound") == "as opened"
    assert fresh.revert("sound") is True

    s = StudioState(tmp_path / "old")
    s.patch = replace(s.patch, cutoff=808.0)
    s.save()
    payload = json.loads((tmp_path / "old" / "state.json").read_text(encoding="utf-8"))
    payload.pop("baselines", None)  # a pre-feature state.json
    (tmp_path / "old" / "state.json").write_text(json.dumps(payload), encoding="utf-8")

    again = StudioState(tmp_path / "old")
    assert again.baseline_label("sound") == "as opened"
    again.patch = replace(again.patch, cutoff=100.0)
    assert again.revert("sound") and again.patch.cutoff == 808.0


def test_changed_dots_track_edits_without_a_page_render(tmp_path):
    """Edits post with hx-swap="none", so the menu rides along out-of-band
    on every render — otherwise the "changed" dot would never appear."""
    client = make_client(tmp_path)
    r = client.post("/fragment/drums", data={"kick.level": "0.4"}, headers=_hx("drums"))
    assert r.status_code == 200
    # morphed: an open menu stays open while an edit elsewhere redraws it
    assert 'id="reset-menu" hx-swap-oob="outerMorph"' in r.text
    assert 'class="dirty-dot"' in r.text  # the kit moved
    # ...and it clears again once that scope is back where it started
    r = client.post(
        "/fragment/reset", data={"scope": "kit", "mode": "revert"}, headers=_hx("drums")
    )
    assert r.status_code == 200
    r = client.post("/fragment/drums", data={}, headers=_hx("drums"))
    assert 'class="dirty-dot"' not in r.text


def test_undo_reverses_one_tweak_and_a_drag_is_one_step(tmp_path, monkeypatch):
    """Bounded patch undo: posts within 1s coalesce into one
    step (a drag is one undo), older steps stack up to the cap."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    base = float(s.patch.cutoff)
    t = iter([0.0, 10.0, 10.2, 10.4, 20.0, 30.0])
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(t))
    client.post("/fragment/patch", data={"cutoff": "500"})
    client.post("/fragment/patch", data={"cutoff": "600"})
    client.post("/fragment/patch", data={"cutoff": "700"})
    client.post("/fragment/patch", data={"cutoff": "900"})
    r = client.post("/fragment/undo")  # undoes the 600/700/900 drag as one
    assert r.status_code == 200
    assert float(s.patch.cutoff) == 500.0
    client.post("/fragment/undo")
    assert float(s.patch.cutoff) == base


def test_another_control_inside_the_window_is_a_step_of_its_own(tmp_path, monkeypatch):
    """Only posts moving the same fields join a step: an unplug 0.4 s after
    a patch-bay drag was folded into the drag's step, whose patch was the
    unplugged one, so Ctrl+Z moved nothing (tests/browser/test_bench.py).
    A post that moves no field of the patch is no step at all."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    t = iter([0.0, 0.4, 0.6])
    monkeypatch.setattr("synthlab.studio.state.monotonic", lambda: next(t))
    drag = {"mod2_src": "velocity", "mod2_dst": "drive", "mod2_amt": "0.5"}
    client.post("/fragment/patch", data=drag)
    client.post("/fragment/patch", data={"mod2_src": "none"})  # the unplug
    client.post("/fragment/patch", data={"mod2_src": "none"})  # moves nothing
    assert len(s._history) == 2
    client.post("/fragment/undo")
    assert (s.patch.mod2_src, s.patch.mod2_dst) == ("velocity", "drive")
    client.post("/fragment/undo")
    assert s.patch.mod2_src == "none"
    assert client.post("/fragment/undo").status_code == 204


def test_undo_empty_history_is_a_204(tmp_path):
    client = make_client(tmp_path)
    assert client.post("/fragment/undo").status_code == 204


def test_undo_button_and_ctrl_z_exist(tmp_path):
    client_html = make_client(tmp_path).get("/").text
    assert 'id="undo-btn"' in client_html
    src = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert '"z"' in src and "undo-btn" in src
