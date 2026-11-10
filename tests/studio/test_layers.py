"""Multi-track layers: stash/restore on switch, layered rendering, mute."""

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.arrange import build_song
from synthlab.studio.render import RenderInputs
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement, StudioState
from synthlab.synth import preset


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


LAYER = {
    "patch": {"name": "sub", "osc1": "sine", "cutoff": 300.0},
    "cells": [["A1", 0], ["A1", 2]],
    "muted": False,
    "gain": 1.0,
}


def test_layered_render_adds_audio():
    arr = Arrangement(cells=[])  # active lane empty: only the layer sounds
    alone = build_song(preset("pad_warm"), arr).render()
    layered = build_song(preset("pad_warm"), arr, layers=[LAYER]).render()
    assert np.all(np.isfinite(layered))
    assert float(np.max(np.abs(layered))) > 0.05
    assert float(np.max(np.abs(alone))) < 1e-9


def test_main_muted_silences_active_lane():
    arr = Arrangement()  # default cells present
    loud = build_song(preset("pad_warm"), arr).render()
    muted = build_song(preset("pad_warm"), arr, main_muted=True).render()
    assert float(np.max(np.abs(loud))) > 0.05
    assert float(np.max(np.abs(muted))) < 1e-9


def test_render_key_depends_on_layers():
    arr = Arrangement()
    p = preset("pad_warm")
    assert (
        RenderInputs(patch=p, arr=arr).key()
        != RenderInputs(patch=p, arr=arr, layers=[LAYER]).key()
    )
    assert (
        RenderInputs(patch=p, arr=arr).key()
        != RenderInputs(patch=p, arr=arr, main_muted=True).key()
    )


def test_select_layer_stashes_and_restores(tmp_path):
    s = StudioState(tmp_path)
    s.patch = s.patch.tweak(cutoff=500.0)
    s.arrangement = Arrangement(cells=[["A1", 0]])
    s.select_layer(1)
    # fresh layer inherits the patch but starts with an empty grid
    assert s.patch.cutoff == 500.0
    assert s.arrangement.cells == []
    s.patch = s.patch.tweak(cutoff=900.0)
    s.select_layer(0)
    assert s.patch.cutoff == 500.0
    assert s.arrangement.cells == [["A1", 0]]
    s.select_layer(1)
    assert s.patch.cutoff == 900.0


def test_layer_endpoints_roundtrip(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/patch", data={"cutoff": "500"})
    r = client.post("/fragment/layer/select", data={"index": "1"})
    assert r.status_code == 200
    client.post("/fragment/patch", data={"cutoff": "900"})
    back = client.post("/fragment/layer/select", data={"index": "0"})
    assert 'value="500' in back.text
    assert client.post("/fragment/layer/select", data={"index": "7"}).status_code == 422


def test_layer_mute_toggles_and_rerenders(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/layer/mute", data={"index": "0"})
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    # the layer strip re-renders out of band with the muted state
    assert 'id="layer-strip"' in r.text and 'hx-swap-oob="outerMorph"' in r.text
    assert 'id="l0-m" class="btn btn-s l-tog l-m"' in r.text
    assert 'aria-label="silent: muted"' in r.text


def test_layers_persist(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/fragment/layer/select", data={"index": "2"})
    s = StudioState(tmp_path)
    assert s.active_layer == 2
    assert len(s.layers) == 4


def test_a_three_slot_save_loads_with_a_fourth_empty_slot(tmp_path):
    """Saves from before the fourth slot keep their three layers."""
    with make_client(tmp_path) as client:
        client.post("/fragment/layer/select", data={"index": "2"})
        client.post("/fragment/layer/add")
    before = StudioState(tmp_path)
    kept, active = before.layers[:3], before.active_layer
    assert any(lay.get("patch") for lay in kept)
    path = tmp_path / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["layers"] = data["layers"][:3]
    path.write_text(json.dumps(data), encoding="utf-8")
    s = StudioState(tmp_path)
    assert len(s.layers) == 4 and not s.layers[3].get("patch")
    assert s.layers[:3] == kept
    assert s.active_layer == active
