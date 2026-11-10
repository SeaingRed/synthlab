"""Hostile stored state: every probe of a security review, as a regression.

Each of these reached a 500, a hang or a failed startup through a value an
imported project, a state.json or a stored baseline could carry. They are
kept one-per-finding so a regression names the exact hole that reopened;
the schema-driven sweep in test_schema.py is what keeps the *class*
closed.
"""

import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits
from synthlab.studio.server import create_app
from synthlab.studio.state import RESET_SCOPES, StudioState
from tests.studio.song_helpers import load_section


def _client(data_dir) -> TestClient:
    return TestClient(create_app(data_dir=data_dir))


def _import(client: TestClient, proj) -> int:
    body = (
        proj
        if isinstance(proj, bytes)
        else json.dumps(proj, separators=(",", ":")).encode()
    )
    r = client.post(
        "/project/import", files={"file": ("p.json", body)}, follow_redirects=False
    )
    return r.status_code


def _pages_ok(client: TestClient) -> None:
    for path in ("/", "/view/perform", "/view/drums", "/view/breed", "/view/editor"):
        assert client.get(path).status_code == 200, path
    assert client.get("/export/song.wav").status_code in (200, 404)
    # perform renders every part (synth, drums, layers, clips); the other
    # views render subsets of the same inputs
    r = client.post("/fragment/preview", data={"view": "perform"})
    assert r.status_code in (200, 422), r.status_code


def _revert_everything(s: StudioState, client: TestClient) -> None:
    for scope in RESET_SCOPES:  # reverts accumulate: check once, after all
        s.revert(scope)
    _pages_ok(client)
    for i in range(len(s.song["sections"])):
        load_section(s, i)
        _pages_ok(client)
    for i in range(len(s.layers)):
        s.select_layer(i)
    _pages_ok(client)


# --- CRITICAL: baselines as a list or a str ----------------------------------


@pytest.mark.parametrize("hostile", [[1], "bogus", 5, [["sound", {}]]])
def test_import_with_non_dict_baselines_is_not_a_500(tmp_path, hostile):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["baselines"] = hostile
    assert _import(client, proj) in (303, 422)
    _pages_ok(client)
    _revert_everything(client.app.state.studio, client)


@pytest.mark.parametrize("hostile", [[1], "bogus"])
def test_state_json_with_non_dict_baselines_starts_and_keeps_the_session(
    tmp_path, hostile
):
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=432.0)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["baselines"] = hostile
    (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")
    again = StudioState(tmp_path)  # used to raise AttributeError at startup
    assert again.patch.cutoff == 432.0  # tolerant: one bad key, not a lost session
    assert set(again.baselines) == set(RESET_SCOPES)  # re-marked "as opened"


@pytest.mark.parametrize("hostile", [[1], "bogus", 5, {"x": 1}])
def test_baseline_data_of_the_wrong_type_does_not_break_revert(tmp_path, hostile):
    s = StudioState(tmp_path)
    before = s._payload()["patch"]
    s.baselines["sound"] = {"label": "x", "data": hostile}
    s.revert("sound")  # used to raise AttributeError on a list
    assert s._payload()["patch"] == before
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["baselines"] = {"sound": {"label": "x", "data": hostile}}
    assert _import(client, proj) == 303
    r = client.post(
        "/fragment/reset",
        data={"scope": "sound", "mode": "revert"},
        headers={"HX-Request": "true", "HX-Current-URL": "http://x/view/editor"},
    )
    assert r.status_code in (200, 422)
    _pages_ok(client)


# --- CRITICAL: arr.drums / drum_vel / drum_samples were never cleaned --------

_DRUM_HOSTILES = [
    ("drums", 5),
    ("drums", [["kick", float("inf")]]),
    ("drums", [["kick", "x"], ["nope", 1], "junk", [1, 2, 3]]),
    ("drum_vel", [1]),
    ("drum_vel", {"0": "loud", "x": 1, "5": float("nan")}),
    ("drum_samples", 7),
    ("drum_samples", {"kick": 5, "nope": "clip", "snare": ["x"]}),
]


@pytest.mark.parametrize(("key", "hostile"), _DRUM_HOSTILES)
def test_hostile_drum_fields_import_cleanly_and_survive_restart(tmp_path, key, hostile):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["arrangement"][key] = hostile
    assert _import(client, proj) in (303, 422)
    _pages_ok(client)
    _revert_everything(client.app.state.studio, client)
    client.app.state.studio.save()
    _pages_ok(_client(tmp_path))  # a restart must not inherit the damage


@pytest.mark.parametrize(("key", "hostile"), _DRUM_HOSTILES)
def test_hostile_drum_fields_via_scene_and_pattern_baseline(tmp_path, key, hostile):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    arr = dict(proj["arrangement"])
    arr[key] = hostile
    proj["scenes"] = [
        {
            "name": "s",
            "patch": proj["patch"],
            "arr": arr,
            "curves": {},
            "layers": [],
            "main_muted": False,
            "main_fx": [],
            "master_fx": [],
            "fx_drums": [],
        }
    ]
    proj["song"] = [{"scene": 0, "repeats": 1}]
    proj["baselines"] = {"pattern": {"label": "x", "data": {f"arr.{key}": hostile}}}
    assert _import(client, proj) in (303, 422)
    s: StudioState = client.app.state.studio
    s.revert("pattern")
    _pages_ok(client)
    load_section(s, 0)
    _pages_ok(client)


# --- IMPORTANT: cells neither capped nor deduplicated ------------------------


def test_duplicate_cells_are_deduped_and_preview_stays_fast(tmp_path):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["arrangement"]["cells"] = [["A1", 0]] * 600_000
    proj["arrangement"]["drums"] = [["kick", 0]] * 100_000
    proj["layers"][1] = {
        "patch": proj["patch"],
        "cells": [["A1", 0]] * 100_000,
        "muted": False,
        "gain": 1.0,
        "fx": [],
    }
    assert _import(client, proj) == 303
    s: StudioState = client.app.state.studio
    assert s.arrangement.cells == [["A1", 0]]
    assert s.arrangement.drums == [["kick", 0]]
    assert s.layers[1]["cells"] == [["A1", 0]]
    t = time.monotonic()
    assert client.post("/fragment/preview", data={"view": "perform"}).status_code == 200
    assert time.monotonic() - t < 20


def test_distinct_cells_are_capped(tmp_path):
    s = StudioState(tmp_path)
    proj = s.project_payload()
    pitches = [f"{n}{o}" for o in range(1, 8) for n in "CDEFGAB"]
    proj["arrangement"]["cells"] = [[p, st] for p in pitches for st in range(128)]
    proj["arrangement"]["drums"] = [
        [v, st] for v in ("kick", "snare", "hat", "ohat") * 3 for st in range(128)
    ]
    # a v2 project's scene + running order, migrated into a section
    proj["v"], proj["song"] = 2, [{"scene": 0, "repeats": 1}]
    proj["scenes"] = [{"patch": proj["patch"], "arr": dict(proj["arrangement"])}]
    s.load_project(proj)
    assert len(s.arrangement.cells) == limits.MAX_GRID_CELLS
    assert len(s.arrangement.drums) == 4 * 128  # deduped, under the drum cap
    notes = [p for p in s.song["patterns"].values() if p["kind"] == "notes"]
    assert [len(p["cells"]) for p in notes] == [limits.MAX_GRID_CELLS]


def test_grid_caps_derive_from_the_largest_grid():
    assert limits.MAX_GRID_STEPS == 4 * 32
    assert limits.MAX_GRID_CELLS == limits.MAX_GRID_STEPS * limits.MAX_GRID_PITCHES
    assert limits.MAX_DRUM_CELLS == limits.MAX_GRID_STEPS * 8


# --- IMPORTANT: unhashable values in the enum cleaners -----------------------


@pytest.mark.parametrize("hostile", [[1], {"x": 1}, ["normal"]])
def test_unhashable_wildness_and_audition(tmp_path, hostile):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["breed_wildness"] = hostile
    proj["audition"] = hostile
    assert _import(client, proj) == 303
    s: StudioState = client.app.state.studio
    assert s.breed_wildness == "normal" and s.audition == "hold"
    _pages_ok(client)


# --- MINOR -------------------------------------------------------------------


@pytest.mark.parametrize("hostile", [5, "kick", {"kick": 1}])
def test_drum_bank_slot_with_non_list_steps(tmp_path, hostile):
    s = StudioState(tmp_path)
    proj = s.project_payload()
    proj["drum_bank"][0]["steps"] = hostile
    s.load_project(proj)
    assert s.drum_bank[0]["steps"] == []
    assert s.bank_load(0)


@pytest.mark.parametrize("depth", [5_000, 200_000])
def test_deeply_nested_json_is_a_422(tmp_path, depth):
    client = _client(tmp_path)
    before = client.app.state.studio._payload()
    body = b'{"format": "synthlab-project", "patch": ' + b"[" * depth + b"]" * depth
    assert _import(client, body + b"}") == 422
    assert client.app.state.studio._payload() == before


@pytest.mark.parametrize("hostile", [["cutoff"], ["cutoff", "resonance", "glide"]])
def test_xy_axes_must_be_exactly_two(tmp_path, hostile):
    client = _client(tmp_path)
    proj = client.get("/project.json").json()
    proj["xy_axes"] = hostile
    assert _import(client, proj) == 303
    assert client.app.state.studio.xy_axes == ["cutoff", "resonance"]
    _pages_ok(client)


# --- crash-proof envelopes ---------------------------------------------------


def test_load_survives_any_exception_type(tmp_path, monkeypatch):
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=432.0)
    s.save()

    def boom(self, data):
        raise RuntimeError("an exception type nobody predicted")

    monkeypatch.setattr(StudioState, "_apply", boom)
    again = StudioState(tmp_path)  # must not raise
    assert again.patch.cutoff != 432.0  # defaults
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_import_survives_any_exception_type(tmp_path, monkeypatch):
    client = _client(tmp_path)
    s: StudioState = client.app.state.studio
    proj = client.get("/project.json").json()
    before = s._payload()

    def boom(self, data):
        raise RuntimeError("an exception type nobody predicted")

    monkeypatch.setattr(StudioState, "_apply", boom)
    assert _import(client, proj) == 422
    assert s._payload() == before


def test_a_huge_json_integer_anywhere_is_not_an_overflow(tmp_path):
    """JSON integers are unbounded; float() of a 400-digit one raises
    OverflowError — which params.clamp and clean_chain did not catch."""
    huge = 10**400
    s = StudioState(tmp_path)
    proj = s.project_payload()
    proj["patch"]["cutoff"] = huge
    proj["fx_master"] = [{"kind": "reverb", "params": {"mix": huge}}]
    proj["arrangement"]["bpm"] = huge
    proj["arrangement"]["drum_params"] = {"kick": {"decay": huge}}
    proj["active_layer"] = huge
    s.load_project(proj)
    assert s.patch.cutoff == type(s.patch)().cutoff
    assert 0.0 <= s.fx_master[0]["params"]["mix"] <= 1.0
    assert s.arrangement.bpm == 124.0


# --- preset files on disk are untrusted too ----------------------------------

_DEEP = "[" * 100_000 + "]" * 100_000


def test_deeply_nested_preset_files_are_a_clean_refusal(tmp_path):
    client = _client(tmp_path)
    s: StudioState = client.app.state.studio
    (s.preset_dir() / "deep.json").write_text(_DEEP, encoding="utf-8")
    (s.panel_preset_dir("Filter") / "deep.json").write_text(_DEEP, encoding="utf-8")
    (s.drum_lib_dir() / "deep.json").write_text(_DEEP, encoding="utf-8")
    before = s._payload()
    r = client.post("/fragment/preset/load", data={"name": "deep"})
    assert r.status_code == 422
    r = client.post(
        "/fragment/perform/lane/sound", data={"index": "0", "sound": "preset:deep"}
    )
    assert r.status_code == 422
    r = client.post(
        "/fragment/panel/preset/load", data={"group": "Filter", "name": "deep"}
    )
    assert r.status_code == 422
    assert client.get("/preset/deep/audition.wav").status_code == 404
    assert s.load_drum_pattern("deep") is None
    assert s._payload() == before
    assert client.get("/view/library").status_code == 200  # meta backfill


def test_panel_preset_values_are_clamped(tmp_path):
    s = StudioState(tmp_path)
    group = next(g for g in ("Filter", "Amp", "Oscillators") if s._panel_field_names(g))
    names = s._panel_field_names(group)
    hostile = {n: 1e12 for n in names} | {names[0]: [1e9]}
    (s.panel_preset_dir(group) / "evil.json").write_text(
        json.dumps(hostile), encoding="utf-8"
    )
    s.load_panel_preset(group, "evil")
    from synthlab.params import clamp

    assert clamp(s.patch) == s.patch  # already inside every range
