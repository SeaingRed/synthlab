"""Sound and layer switching on Perform:
the one sound menu (`sounds.menu`, base.html's `#lane-sounds`), the lane
heads' sound ▾ (`POST /fragment/perform/lane/sound`, `StudioState.
lane_sound`: one pattern undo step), and a load that says what it replaces
(`StudioState.load_sound_as`; Synths' Load posts `as`)."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import sounds
from synthlab.studio.factory import FACTORY
from synthlab.studio.recipes import cards
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState

PLAIN = FACTORY[3]["name"]


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _two_lanes(s: StudioState) -> None:
    """Lane 1 with notes, lane 2 a sound of its own with its own notes."""
    s.arrangement = replace(s.arrangement, cells=[["A1", 0], ["A1", 4]])
    s.add_layer(replace(s.patch, name="Second", cutoff=900.0), select=False)
    s.set_follow(1, None)
    s.layers[1]["cells"] = [["E2", 8]]


def _layered_preset(s: StudioState, name: str) -> None:
    """A saved sound with a layer of its own, then the session back as it was."""
    was = s.patch
    s.add_layer(replace(s.patch, name="its pad"), select=False)
    s.save_preset(name, include_layers=True)
    s.clear_layer(1)
    s.patch = was


def _lane(client, index, sound, how="") -> object:
    data = {"index": str(index), "sound": sound, "as": how}
    return client.post(
        "/fragment/perform/lane/sound", data=data, headers=_hx("perform")
    )


def _undo(client):
    return client.post("/fragment/pattern/undo", headers=_hx("perform"))


# --- the one menu -------------------------------------------------------------------


def test_the_menu_holds_every_sound_once_a_page_and_marks_layers_and_stars(client):
    s = client.app.state.studio
    _layered_preset(s, "Starlight stack")
    s.save_preset("Acid bass")
    s.toggle_star("Acid bass")
    groups = dict(sounds.menu(s))
    assert list(groups)[0] == "Yours"
    # ★ first, as Synths lists them; a layered sound says so
    assert groups["Yours"] == [
        ("preset", "Acid bass", False, True),
        ("preset", "Starlight stack", True, False),
    ]
    assert ("factory", PLAIN, False, False) in groups["Factory"]
    layered = next(c["name"] for c in cards() if c["layered"])
    assert ("recipes", layered, True, False) in groups["Recipes · with their FX"]
    assert any(label.startswith("Core · ") for label in groups)
    for view in ("perform", "editor", "song", "drums"):
        page = client.get(f"/view/{view}").text
        assert page.count('<template id="lane-sounds">') == 1, view
        assert page.count('id="sound-pick"') == 1, view
    page = client.get("/view/perform").text
    assert '<option value="preset:Starlight stack" data-layered>' in page
    assert '<option value="preset:Acid bass" data-star>' in page
    # a view swap redraws no page frame: the menu asks for itself again
    s.save_preset("Saved since")
    again = client.get("/fragment/sound-menu").text
    assert '<template id="lane-sounds">' in again
    assert '<option value="preset:Saved since">' in again


def test_the_lane_heads_draw_each_lane_its_sound_mute_solo_and_the_next_slot(client):
    s = client.app.state.studio
    _two_lanes(s)
    s.set_layer_muted(1, True)
    html = client.get("/view/perform").text
    heads = html[html.index('class="tcluster lane-heads"') :]
    heads = heads[: heads.index("</fieldset>")]
    assert 'data-stack="2"' in heads
    assert heads.count('hx-post="/fragment/layer/select"') == 2
    assert heads.count("data-sound-menu=") == 3  # two lanes and the next empty slot
    assert 'data-sound-menu="2" data-lane-name=""' in heads
    assert heads.count('hx-post="/fragment/layer/mute"') == 2
    assert heads.count('hx-post="/fragment/layer/solo"') == 2
    assert 'id="lh2-m" class="lh-tog lh-m" hx-post="/fragment/layer/mute"' in heads
    assert "unheard" in heads.split('data-lane-index="1"')[0].split("lane-head")[-1]
    assert (
        'data-keys="Alt+S"' in heads.split('data-lane-index="1"')[0]
    )  # the edited lane's
    assert 'hx-vals=\'{"own": "1"}\'' in heads
    # the Sounds launcher went: its job is the menu's
    assert "sound-launcher" not in html and "/fragment/perform/sound/" not in html


# --- a lane's sound ------------------------------------------------------------------


def test_a_lanes_sound_lands_in_that_lane_and_one_undo_puts_it_back(client):
    s = client.app.state.studio
    _two_lanes(s)
    svc = client.app.state.svc
    before = svc.render_inputs("perform").key()
    first = s.patch
    r = _lane(client, 1, f"factory:{PLAIN}")
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True
    # lane 2 is the edited one now, with the new sound and its own notes
    assert s.active_layer == 1 and s.patch.name == PLAIN
    assert s.arrangement.cells == [["E2", 8]] and not s.follows(1)
    # lane 1 kept its sound and its notes
    assert s.layers[0]["patch"]["name"] == first.name
    assert s.layers[0]["cells"] == [["A1", 0], ["A1", 4]]
    assert "lh2-name" in r.text  # the heads come back with the new name
    # Ctrl+Z on Perform: the sound as it was, on the lane it was taken on
    assert _undo(client).status_code == 200
    assert s.active_layer == 1 and s.patch.name == "Second"
    assert s.patch.cutoff == 900.0
    s.select_layer(0)
    assert svc.render_inputs("perform").key() == before


def test_an_empty_lane_head_makes_a_lane_of_its_own_and_undo_takes_it_away(client):
    s = client.app.state.studio
    _two_lanes(s)
    svc = client.app.state.svc
    before = svc.render_inputs("perform").key()
    was = s.patch
    r = _lane(client, 2, f"factory:{PLAIN}")
    assert r.status_code == 200
    assert s.active_layer == 2 and s.patch.name == PLAIN
    assert not s.follows(2) and s.arrangement.cells == []  # its own empty grid
    assert s.main_index() == 0
    _undo(client)  # back on the lane it was made from, the new one gone
    assert s.active_layer == 0 and s.patch == was
    assert not s.layers[2].get("patch")
    assert svc.render_inputs("perform").key() == before


def test_what_a_load_replaces(client):
    s = client.app.state.studio
    _layered_preset(s, "Starlight stack")
    _two_lanes(s)
    second = dict(s.layers[1])
    # "" as always: a sound with layers brings them, over the other lanes
    _lane(client, 0, "preset:Starlight stack")
    assert s.layers[1]["patch"]["name"] == "its pad" and s.follows(1)
    _undo(client)
    assert s.layers[1] == second
    # this layer: the other lanes stay
    _lane(client, 0, "preset:Starlight stack", "layer")
    assert s.patch.name == "Starlight stack" and s.layers[1] == second
    _undo(client)
    # the whole sound, for one with no layers: the other lanes go
    _lane(client, 0, f"factory:{PLAIN}", "whole")
    assert s.patch.name == PLAIN
    assert not any(lay.get("patch") for lay in s.layers[1:])
    _undo(client)
    assert s.layers[1] == second
    # add as a layer: one more, following the main lane; the edited lane stays
    was = s.patch
    _lane(client, 0, f"factory:{PLAIN}", "add")
    assert s.active_layer == 0 and s.patch == was
    assert s.layers[2]["patch"]["name"] == PLAIN and s.follows(2)
    _undo(client)
    assert not s.layers[2].get("patch")


def test_a_lanes_sound_is_refused_cleanly(client):
    s = client.app.state.studio
    for data in (
        {"index": "9", "sound": f"factory:{PLAIN}"},
        {"index": "x", "sound": f"factory:{PLAIN}"},
        {"index": "0", "sound": "nope:x"},
        {"index": "0", "sound": "factory:"},
        {"index": "0", "sound": f"factory:{PLAIN}", "as": "everything"},
    ):
        r = client.post(
            "/fragment/perform/lane/sound", data=data, headers=_hx("perform")
        )
        assert r.status_code == 422, data
    r = _lane(client, 0, "factory:no such sound")
    assert r.status_code == 422 and "gone" in r.text
    for _ in (1, 2, 3):
        s.add_layer(s.patch, select=False)
    r = _lane(client, 0, f"factory:{PLAIN}", "add")
    assert r.status_code == 422 and "Every layer" in r.text


# --- Synths' Load says what it replaces --------------------------------------------


def test_synths_load_takes_what_it_replaces(client):
    s = client.app.state.studio
    _two_lanes(s)
    second = dict(s.layers[1])
    post = lambda how: client.post(  # noqa: E731
        "/fragment/factory/load", data={"name": PLAIN, "as": how}, headers=_hx("editor")
    )
    assert post("layer").status_code == 200
    assert s.patch.name == PLAIN and s.layers[1] == second
    assert post("whole").status_code == 200
    assert not any(lay.get("patch") for lay in s.layers[1:])
    assert post("add").status_code == 200
    assert s.active_layer == 1 and s.follows(1)  # as ＋ layer: the new one edited
    assert post("bogus").status_code == 422
    # the stack it asks over, and the cards it asks for
    _layered_preset(s, "Starlight stack")
    page = client.get("/view/editor").text
    assert 'data-stack="2"' in page
    assert 'data-name="Starlight stack" data-layered' in page
