"""Breed's layered cards on the page: the depth select, the card's chips
and its two Uses, the overwrite confirm, and "Before last load" bringing a
whole stack back. The audition == Use parity of a layered card (whole
stack, and "main only" == "Use sound") is held by test_sound_sources."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.factory import FACTORY
from synthlab.studio.server import create_app
from synthlab.studio.state import audible_layers

BREED = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/breed"}


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _deep(client: TestClient) -> str:
    return client.post(
        "/fragment/breed/generation", data={"depth": "deep"}, headers=BREED
    ).text


def _ids(html: str) -> list[str]:
    return re.findall(r'data-tile-id="([a-f0-9]{12})"', html)


def test_depth_select_saves_on_change(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    assert s.breed_depth == "single"
    page = client.get("/view/breed").text
    assert 'id="depth"' in page and 'hx-include="#wildness, #depth"' in page
    r = client.post("/fragment/breed/depth", data={"depth": "deep"}, headers=BREED)
    assert r.status_code == 204 and s.breed_depth == "deep"
    client.post("/fragment/breed/depth", data={"depth": "bogus"}, headers=BREED)
    assert s.breed_depth == "deep"
    assert re.search(r'<option value="deep"\s+selected', client.get("/view/breed").text)
    # a generation posting its depth keeps it too
    client.post("/fragment/breed/generation", data={"depth": "layered"}, headers=BREED)
    assert s.breed_depth == "layered"


def test_a_layered_card_shows_its_parts_and_two_uses(tmp_path):
    client = make_client(tmp_path)
    html = _deep(client)
    assert html.count('class="tile ') == 8
    assert html.count(">Use with its layers<") == 8
    assert html.count(">Use sound<") == 8
    assert html.count("▶ main only") == 8
    assert 'data-note="/breed/' in html and "audition.wav?main=1" in html
    assert re.search(r'class="chip role-main">MAIN \w+', html)
    assert re.search(r'class="chip role-(sub|pad|pluck|air)"', html)
    # a single-depth card is as it was
    single = client.post("/fragment/breed/generation", data={"depth": "single"}).text
    assert (
        single.count(">Use on Synths<") == 8 and ">Use with its layers<" not in single
    )
    assert 'class="chip' not in single


def test_use_with_layers_confirms_when_it_replaces_lanes(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    assert "hx-confirm" not in _deep(client)  # nothing of yours to replace
    s.layers[1] = {**s.layers[1], "patch": s.patch.to_json(), "cells": [["A1", 0]]}
    s.layers[2] = {**s.layers[2], "patch": s.patch.to_json(), "cells": [["C2", 1]]}
    html = _deep(client)
    assert html.count("replaces 2 lanes holding notes of their own") == 8
    # "Use sound" keeps your lanes: it never asks
    for block in re.findall(
        r'<button class="btn btn-s t-use alt".*?</button>', html, re.S
    ):
        assert "hx-confirm" not in block


def test_before_last_load_brings_the_whole_stack_back(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    mine = {**s.layers[1], "patch": s.patch.to_json(), "cells": [["A1", 0]]}
    s.layers[1] = dict(mine)
    s.curves = {"cutoff": [[0.0, 0.2], [1.0, 0.6]]}
    s.layers[s.active_layer]["fx"] = [{"kind": "chorus", "params": {}}]
    before_fx = list(s.layers[s.active_layer]["fx"])
    before_patch = s.patch.to_json()
    store = client.app.state.svc.space_store()
    # a card whose main brings FX: the patch-only load at the end drops them
    tid = next(t for t in _ids(_deep(client)) if store.get(t)["sound"]["main_fx"])
    client.post("/fragment/breed/adopt", data={"id": tid}, headers=BREED)
    assert any(lay.get("follow") for lay in s.layers)
    assert s.layers[1]["cells"] == []
    client.post("/fragment/restore_prev", headers=BREED)
    assert s.patch.to_json() == before_patch
    assert s.layers[1]["cells"] == [["A1", 0]]
    assert s.curves == {"cutoff": [[0.0, 0.2], [1.0, 0.6]]}
    assert [u["kind"] for u in s.layers[s.active_layer]["fx"]] == [
        u["kind"] for u in before_fx
    ]
    # and again: the Sound is back (a swap, never a loss)
    client.post("/fragment/restore_prev", headers=BREED)
    assert any(lay.get("follow") for lay in s.layers)
    # a patch-only load afterwards drops the Sound's own FX, so "before
    # last load" keeps them; the one after that is patch-only
    name = FACTORY[3]["name"]
    client.post("/fragment/factory/load", data={"name": name}, headers=BREED)
    assert s.stack_prev is not None and s.layers[s.active_layer]["fx"] == []
    client.post("/fragment/factory/load", data={"name": name}, headers=BREED)
    assert s.stack_prev is None


def test_use_sound_keeps_your_lanes(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    mine = {**s.layers[1], "patch": s.patch.to_json(), "cells": [["A1", 0]]}
    s.layers[1] = dict(mine)
    tid = _ids(_deep(client))[0]
    row = client.app.state.svc.space_store().get(tid)
    client.post("/fragment/breed/adopt", data={"id": tid, "main": "1"}, headers=BREED)
    assert s.layers[1]["cells"] == [["A1", 0]]
    assert s.layers[s.active_layer]["fx"] == row["sound"]["main_fx"]
    assert s.curves == row["sound"]["curves"]


def test_use_with_its_layers_is_heard_with_its_layers(tmp_path):
    # "▶ hears: This layer" left on from an earlier edit used to swallow a
    # layered Sound's lanes: the keys and ▶ played its main lane alone
    client = make_client(tmp_path)
    s = client.app.state.studio
    s.layer_focus = True
    tid = _ids(_deep(client))[0]
    client.post("/fragment/breed/adopt", data={"id": tid}, headers=BREED)
    assert not s.layer_focus
    live = client.get("/patch.json?view=editor").json()
    assert live["layers"], "the Sound's layers are not on the keys"
    assert len(audible_layers(s, "editor")) > 1  # and ▶ plays them


def test_use_sound_leaves_this_layer_as_it_was(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    s.layer_focus = True
    tid = _ids(_deep(client))[0]
    client.post("/fragment/breed/adopt", data={"id": tid, "main": "1"}, headers=BREED)
    assert s.layer_focus


def _adopt_with_fx(client: TestClient) -> list:
    """Use sound on a card whose main lane brings FX; the lane's FX after."""
    store = client.app.state.svc.space_store()
    s = client.app.state.studio
    for tid in _ids(_deep(client)):
        if store.get(tid)["sound"]["main_fx"]:
            client.post(
                "/fragment/breed/adopt", data={"id": tid, "main": "1"}, headers=BREED
            )
            return list(s.layers[s.active_layer]["fx"])
    raise AssertionError("no card in the litter brings FX")


def test_a_patch_only_load_drops_the_last_sounds_own_fx(tmp_path):
    # a card's FX (a 170 Hz highpass, say) used to stay on the lane when a
    # Library/factory sound came next, and quietly gutted every bass after
    client = make_client(tmp_path)
    s = client.app.state.studio
    brought = _adopt_with_fx(client)
    assert brought
    client.post(
        "/fragment/factory/load", data={"name": FACTORY[3]["name"]}, headers=BREED
    )
    assert s.layers[s.active_layer]["fx"] == []
    # before last load brings the card and its FX back
    client.post("/fragment/restore_prev", headers=BREED)
    assert s.layers[s.active_layer]["fx"] == brought


def test_a_patch_only_load_keeps_fx_you_changed(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    mine = _adopt_with_fx(client) + [{"kind": "chorus", "params": {}}]
    s.layers[s.active_layer]["fx"] = mine
    client.post(
        "/fragment/factory/load", data={"name": FACTORY[3]["name"]}, headers=BREED
    )
    assert s.layers[s.active_layer]["fx"] == mine


def test_after_use_the_layer_strip_says_who_follows(tmp_path):
    client = make_client(tmp_path)
    tid = _ids(_deep(client))[0]
    page = client.post("/fragment/breed/adopt", data={"id": tid}, headers=BREED).text
    assert re.search(r'class="l-what">follows main ([+−]\d oct|±0)<', page)
    assert 'class="l-badge badge badge-accent main"' in page


def test_the_measuring_note_of_a_layered_card_is_its_stack(tmp_path):
    client = make_client(tmp_path)
    tid = _ids(_deep(client))[0]
    r = client.get(f"/breed/note/{tid}.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
