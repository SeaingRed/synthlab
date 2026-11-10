"""Each view renders only its own parts: Editor/Bass = synth, Drums = kit,
Perform = everything. A problem in one workshop must not break another."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.render import RenderInputs, render_audio
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement, StudioState
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _arr():
    return Arrangement(cells=[["A1", 0]], drums=[["kick", 0], ["kick", 2]])


def test_render_parts_select_sections():
    p, a = Patch(), _arr()
    both = render_audio(RenderInputs(patch=p, arr=a))
    synth = render_audio(RenderInputs(patch=p, arr=a, parts=("synth",)))
    drums = render_audio(RenderInputs(patch=p, arr=a, parts=("drums",)))
    assert (
        np.abs(both).max() > 0 and np.abs(synth).max() > 0 and np.abs(drums).max() > 0
    )
    assert not np.allclose(synth, drums)
    assert (
        np.abs(
            render_audio(
                RenderInputs(patch=p, arr=replace(a, cells=[]), parts=("synth",))
            )
        ).max()
        == 0
    )
    assert (
        np.abs(
            render_audio(
                RenderInputs(patch=p, arr=replace(a, drums=[]), parts=("drums",))
            )
        ).max()
        == 0
    )


def test_render_key_includes_parts():
    p, a = Patch(), _arr()
    assert (
        RenderInputs(patch=p, arr=a, parts=("synth",)).key()
        != RenderInputs(patch=p, arr=a, parts=("drums",)).key()
    )
    assert (
        RenderInputs(patch=p, arr=a).key()
        == RenderInputs(patch=p, arr=a, parts=("synth", "drums")).key()
    )


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://x/view/{view}"}


def test_drums_actions_survive_a_broken_editor_loop(tmp_path, monkeypatch):
    from synthlab.studio import render

    monkeypatch.setattr(render, "LOOP_CAP_SECONDS", 10.0)  # the real one is 72 s
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    # 4 bars @ 40 bpm = 24 s: over a 10 s loop cap — but that is the
    # *synth* grid's length; the drum machine keeps its own bars
    s.arrangement = replace(s.arrangement, bars=4, bpm=40.0, drum_bars=1)
    r = client.post(
        "/fragment/drums/bank/save/3", data={"name": "kept"}, headers=_hx("drums")
    )
    assert r.status_code == 200  # the save happened ...
    assert s.drum_bank[3]["name"] == "kept"
    trig = json.loads(r.headers["HX-Trigger"])
    assert "preview-ready" in trig  # ... and the drums still preview (1 bar @ 40)
    assert 'id="drum-bank"' in r.text
    # a drum loop that really is too long still toasts instead of failing
    s.arrangement = replace(s.arrangement, drum_bars=4)
    r = client.post(
        "/fragment/drums/bank/save/4", data={"name": "long"}, headers=_hx("drums")
    )
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "studio-toast" in trig and "cap" in trig["studio-toast"]["message"]


def test_drums_preview_ignores_synth_state(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    # a synth-side failure: curves that can't apply would raise in the main section;
    # simplest proxy — mute nothing, but a drums-only key must not change with the patch
    r1 = client.post("/fragment/preview", data={"view": "drums"})
    k1 = json.loads(r1.headers["HX-Trigger"])["preview-ready"]["url"]
    s.patch = replace(s.patch, cutoff=300.0, osc1="square")
    r2 = client.post("/fragment/preview", data={"view": "drums"})
    k2 = json.loads(r2.headers["HX-Trigger"])["preview-ready"]["url"]
    assert k1 == k2
    r3 = client.post("/fragment/preview", data={"view": "editor"})
    assert json.loads(r3.headers["HX-Trigger"])["preview-ready"]["url"] != k1


def test_editor_transport_has_no_drum_rows_and_keeps_drums(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/").text
    assert 'name="drum.kick.0"' not in html
    s: StudioState = client.app.state.studio
    client.post("/fragment/drums/bank/load/0", headers=_hx("drums"))
    before = list(s.arrangement.drums)
    client.post("/fragment/drums", data={"kick.tune": "40"})
    assert before
    # editor posts (no drum.* cells) must not wipe the beat or the kit params
    client.post(
        "/fragment/arrangement",
        data={"bpm": "120", "cell.A1.0": "on"},
        headers=_hx("editor"),
    )
    assert s.arrangement.drums == before
    assert s.arrangement.drum_params["kick"]["tune"] == 40.0
    assert s.arrangement.bpm == 120.0


def test_drums_view_has_its_own_transport(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/drums").text
    assert 'id="drum-transport"' in html and 'id="play"' in html
    assert "drums only" in html
    r = client.post(
        "/fragment/drums/transport",
        data={"bpm": "133", "swing": "0.2"},
        headers=_hx("drums"),
    )
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert (s.arrangement.bpm, s.arrangement.drum_swing) == (133.0, 0.2)
    assert s.arrangement.swing == 0.0  # the synth grid's swing is untouched


def test_view_navigation_cues_that_views_mix(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/bank/load/0", headers=_hx("drums"))
    urls = {}
    for view in ("editor", "drums", "perform"):
        r = client.get(f"/view/{view}", headers={"HX-Request": "true"})
        assert r.status_code == 200
        urls[view] = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    assert len(set(urls.values())) == 3


def test_perform_launch_renders_everything(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    from synthlab.studio import song_model as sm

    pid = sm.add_pattern(
        s.song, "drums", "b", {"bars": 1, "steps": 16, "drums": [["kick", 4]]}
    )
    r = client.post(f"/fragment/perform/launch/{pid}", headers=_hx("perform"))
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    key = url.split("/")[-1].split(".")[0]

    # perform renders the clip lane too, and keys carry clip_root
    assert (
        key
        == RenderInputs(
            patch=s.patch,
            arr=s.arrangement,
            curves=s.curves,
            layers=s.other_layers(),
            main_muted=False,
            main_fx=s.layers[s.active_layer].get("fx") or [],
            master_fx=s.fx_master,
            parts=("synth", "drums", "clips"),
            clip_root=s.data_dir,
        ).key()
    )
