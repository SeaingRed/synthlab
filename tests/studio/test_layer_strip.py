"""The Synths layer strip: Follow main on any layer, solo, level, the ▶
listening toggle (all layers / this layer), add and clear.

The user problem this pins: on Synths ▶ played the reference phrase on the
edited layer only; every other layer played its own Perform notes, so a
layer with none was silent and stacking sounds on Synths did nothing. Now
any layer can follow the main lane (the phrase on a bench view), and the
main lane keeps its place while another layer is edited.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.arrange import follow_layer
from synthlab.studio.factory import FACTORY
from synthlab.studio.render import render_audio
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState
from synthlab.synth import Patch

ED = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
PERFORM = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _two_layers(client) -> StudioState:
    """Layer 1 a saw (the main lane, no Perform notes), layer 2 a sine sub
    with no notes of its own — it plays the phrase on Synths too (silent
    there, "All layers" would hear two of three)."""
    s = client.app.state.studio
    s.patch = Patch(name="lead", osc1="saw", cutoff=2500.0)
    s.layers[1] = {
        **s.layers[1],
        "patch": Patch(name="sub", osc1="sine", cutoff=400.0).to_json(),
        "cells": [],
    }
    return s


def _post(client, path, data=None, headers=ED):
    return client.post(path, data=data or {}, headers=headers)


# --- Follow main ---------------------------------------------------------------


def test_a_layer_with_no_notes_plays_the_phrase_and_follow_moves_its_octave(tmp_path):
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    alone = svc.render_inputs("editor")
    # heard on ▶ with no notes of its own: the phrase stands in for them
    assert len(alone.layers) == 1
    # (it follows the phrase at its own pitch, resolved at render time)
    assert follow_layer(alone.layers[0], alone.arr)["cells"] == alone.arr.cells
    row = svc.layer_context()["strip"][1]
    assert row["hear"] and row["plays"] == "plays the phrase (no notes of its own)"
    # in the loop (Perform) it still has nothing to play
    assert not svc.render_inputs("perform").layers
    # the keys hear it too (a key is the phrase's note there)
    assert [ln.slot for ln in s.lanes(bench=True, keys=True)] == [0, 1]
    r = _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    assert s.layers[1]["follow"] == {"octave": -1}
    inp = svc.render_inputs("editor")
    assert len(inp.layers) == 1 and inp.key() != alone.key()
    # render parity: the follower plays the phrase an octave down, exactly
    # as a plain layer holding those notes would
    plain = follow_layer(inp.layers[0], inp.arr)
    assert plain["cells"] == [
        [c[0].replace(c[0][-1], str(int(c[0][-1]) - 1)), c[1]] for c in inp.arr.cells
    ]
    np.testing.assert_array_equal(
        render_audio(inp), render_audio(replace(inp, layers=[plain]))
    )
    assert not np.array_equal(render_audio(inp), render_audio(alone))
    assert svc.layer_context()["strip"][1]["hear"]


def test_follow_off_gives_back_its_own_notes_and_the_old_key(tmp_path):
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    s.layers[1]["cells"] = [["A2", 0]]
    before = svc.render_inputs("perform").key()
    bench = svc.render_inputs("editor").key()
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "0"})
    assert svc.render_inputs("editor").key() != bench
    _post(client, "/fragment/layer/follow", {"index": "1", "on": "0"})
    assert "follow" not in s.layers[1]
    assert s.layers[1]["cells"] == [["A2", 0]]  # kept while it followed
    assert svc.render_inputs("perform").key() == before
    assert svc.render_inputs("editor").key() == bench


def test_the_follow_octave_is_heard(tmp_path):
    client = _client(tmp_path)
    _two_layers(client)
    svc = client.app.state.svc
    keys = set()
    for octave in ("-2", "-1", "0", "1"):
        _post(client, "/fragment/layer/follow", {"index": "1", "octave": octave})
        keys.add(svc.render_inputs("editor").key())
    assert len(keys) == 4
    bad = _post(client, "/fragment/layer/follow", {"index": "1", "octave": "3"})
    assert bad.status_code == 422


def test_the_main_lane_cannot_follow_itself(tmp_path):
    client = _client(tmp_path)
    s = _two_layers(client)
    r = _post(client, "/fragment/layer/follow", {"index": "0", "octave": "-1"})
    assert r.status_code == 422 and "main lane" in r.text
    assert "follow" not in s.layers[0]
    strip = client.app.state.svc.layer_context()["strip"]
    assert strip[0]["main"] and strip[0]["editing"]


def test_the_main_lane_stays_put_while_a_follower_is_edited(tmp_path):
    """Editing a follower keeps it following (its panels, its sound) and
    the main lane still plays the phrase on Synths: the stack you hear is
    the same whichever layer the panels edit."""
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    stack = svc.render("editor").audio()
    _post(client, "/fragment/layer/select", {"index": "1"})
    assert s.active_layer == 1 and s.main_index() == 0 and s.follows(1)
    inp = svc.render_inputs("editor")
    # the edited follower plays the phrase at its octave, the main the phrase
    assert inp.patch.name == "sub"
    assert [c[1] for c in inp.arr.cells] == [c[1] for c in inp.layers[0]["cells"]]
    np.testing.assert_allclose(svc.render("editor").audio(), stack, atol=1e-6)
    strip = svc.layer_context()["strip"]
    assert strip[0]["main"] and not strip[0]["editing"] and strip[0]["hear"]
    assert strip[1]["editing"] and strip[1]["octave"] == -1


def test_perform_says_when_the_edited_layer_follows(tmp_path):
    client = _client(tmp_path)
    s = _two_layers(client)
    s.arrangement = replace(s.arrangement, cells=[["A2", 0], ["C3", 4]])
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    assert "lane-follow-note" not in client.get("/view/perform").text
    _post(client, "/fragment/layer/select", {"index": "1"})
    page = client.get("/view/perform").text
    assert "Layer 2 follows Layer 1's notes −1 oct" in page
    # and the loop plays the main lane's notes an octave down on it
    assert s.lane_arrangement().cells == [["A1", 0], ["C2", 4]]


# --- mute / solo -------------------------------------------------------------


def test_solo_hears_only_soloed_layers_and_mute_wins(tmp_path):
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    both = svc.render_inputs("editor")
    r = _post(client, "/fragment/layer/solo", {"index": "1"})
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    solo = svc.render_inputs("editor")
    assert solo.main_muted and len(solo.layers) == 1  # only the soloed layer
    assert solo.key() != both.key()
    assert all("solo" not in lay for lay in solo.layers)  # never render input
    strip = svc.layer_context()["strip"]
    assert strip[0]["why"] == "another layer is soloed" and strip[1]["hear"]
    _post(client, "/fragment/layer/mute", {"index": "1"})  # mute wins
    assert not svc.render_inputs("editor").layers
    _post(client, "/fragment/layer/mute", {"index": "1"})
    _post(client, "/fragment/layer/solo", {"index": "1"})
    assert svc.render_inputs("editor").key() == both.key()
    # a save carries every layer (solo is monitoring, not the sound)
    _post(client, "/fragment/layer/solo", {"index": "0"})
    s.save_preset("stack", include_layers=True)
    saved = json.loads((s.preset_dir() / "stack.json").read_text(encoding="utf-8"))
    assert len(saved["layers"]) == 1 and "solo" not in saved["layers"][0]


def test_solo_persists_only_when_set(tmp_path):
    with _client(tmp_path) as client:
        _two_layers(client)
        _post(client, "/fragment/layer/solo", {"index": "1"})
    s = StudioState(tmp_path)
    assert s.layers[1]["solo"] is True
    assert "solo" not in s.layers[0] and "solo" not in s.layers[2]


# --- level ---------------------------------------------------------------------


def test_the_level_slider_sets_the_layer_gain(tmp_path):
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "0"})
    before = svc.render_inputs("editor").key()
    r = _post(client, "/fragment/layer/gain", {"index": "1", "gain": "0.4"})
    assert r.status_code == 200 and s.layers[1]["gain"] == 0.4
    assert svc.render_inputs("editor").key() != before
    _post(client, "/fragment/layer/gain", {"index": "0", "gain": "9"})
    assert s.layers[0]["gain"] == 1.5  # clamped to the schema's range
    assert svc.render_inputs("editor").main_gain == 1.5
    bad = _post(client, "/fragment/layer/gain", {"index": "1", "gain": "nan"})
    assert bad.status_code == 422


# --- ▶ hears: all layers / this layer --------------------------------------------


def test_this_layer_focuses_the_bench_preview_only(tmp_path):
    with _client(tmp_path) as client:
        s, svc = _two_layers(client), client.app.state.svc
        s.arrangement = replace(s.arrangement, cells=[["A2", 0]])
        _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
        _post(client, "/fragment/layer/mute", {"index": "0"})
        loop = svc.render_inputs("perform").key()
        r = _post(client, "/fragment/layer/focus", {"on": "1"})
        assert r.status_code == 200 and s.layer_focus
        inp = svc.render_inputs("editor")
        # only the edited layer, heard even while muted — that is the focus
        assert not inp.layers and not inp.main_muted
        assert svc.render_inputs("perform").key() == loop  # the loop is not the bench
        page = client.get("/").text
        assert 'id="ls-this" class="ls-seg"' in page
        assert "▶ is set to This layer" in page
    assert StudioState(tmp_path).layer_focus  # persisted per session
    client = _client(tmp_path)
    _post(client, "/fragment/layer/focus", {"on": "0"})
    assert not client.app.state.studio.layer_focus


# --- add / clear ---------------------------------------------------------------


def test_add_layer_copies_the_sound_into_a_following_slot_and_edits_it(tmp_path):
    client = _client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    s.patch = Patch(name="bell", osc1="triangle")
    r = _post(client, "/fragment/layer/add")
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    assert s.active_layer == 1 and s.main_index() == 0
    assert s.layers[1]["follow"] == {"octave": 0} and s.patch.name == "bell"
    # ▶ on Synths now plays both layers on the phrase
    assert len(svc.render_inputs("editor").layers) == 1
    name = FACTORY[2]["name"]
    _post(client, "/fragment/layer/add", {"source": "factory", "name": name})
    assert s.active_layer == 2 and s.patch.name == name
    assert len(svc.render_inputs("editor").layers) == 2
    _post(client, "/fragment/layer/add")
    assert s.active_layer == 3 and len(svc.render_inputs("editor").layers) == 3
    full = _post(client, "/fragment/layer/add")
    assert full.status_code == 422 and "clear one" in full.text
    assert _post(client, "/fragment/layer/add", {"source": "nope"}).status_code == 404


def test_a_picked_sound_is_never_refused_it_replaces_the_edited_one(tmp_path):
    """The user problem: four layers, ＋ on a sound in the list, and a toast
    said to clear one first. A picked sound lands: over the edited layer."""
    client = _client(tmp_path)
    s = client.app.state.studio
    for _ in range(3):
        _post(client, "/fragment/layer/add")
    assert s.free_slot() is None and s.active_layer == 3
    was, name = s.patch.name, FACTORY[2]["name"]
    r = _post(client, "/fragment/layer/add", {"source": "factory", "name": name})
    assert r.status_code == 200 and s.active_layer == 3 and s.patch.name == name
    assert all(lay.get("patch") for i, lay in enumerate(s.layers) if i != 3)
    assert s.swap_prev() and s.patch.name == was  # "before last load" has the old one


def test_add_on_a_song_track_loads_into_the_track(tmp_path):
    from synthlab.studio import song_model as sm

    client = _client(tmp_path)
    s = client.app.state.studio
    tid = sm.add_track(s.song, "synth", "Lead", patch=Patch(name="old").to_json())
    assert s.edit_track(tid)
    name = FACTORY[2]["name"]
    r = _post(client, "/fragment/layer/add", {"source": "factory", "name": name})
    assert r.status_code == 200 and s.song_edit_track == tid and s.active_layer == 0
    track = s.song["tracks"][sm.track_index(s.song, tid)]
    assert track["patch"]["name"] == name
    # ✕ on the edited row would end the edit and empty a layer of the loop
    # that the panels never showed (review finding): refused, and drawn off
    s.layers[1] = {**s.layers[1], "patch": Patch(name="sub").to_json()}
    assert not client.app.state.svc.layer_context()["can_clear_edited"]
    r = _post(client, "/fragment/layer/clear", {"index": "0"})
    assert r.status_code == 422 and "song track" in r.text
    assert s.song_edit_track == tid


def test_a_whole_sound_added_over_a_full_stack_replaces_one_lane_only(tmp_path):
    """Review finding: ＋ with every slot full went through the whole-Sound
    load, and a recipe with layers replaced all four lanes."""
    from synthlab.studio.recipes import cards, get_recipe

    client = _client(tmp_path)
    s = client.app.state.studio
    for _ in range(3):
        _post(client, "/fragment/layer/add")
    layered = next(c["name"] for c in cards() if get_recipe(c["name"]).get("layers"))
    others = [dict(lay) for i, lay in enumerate(s.layers) if i != s.active_layer]
    r = _post(client, "/fragment/layer/add", {"source": "recipes", "name": layered})
    assert r.status_code == 200 and s.patch.name == layered
    assert [
        dict(lay) for i, lay in enumerate(s.layers) if i != s.active_layer
    ] == others


def test_add_from_the_library_lands_on_synths(tmp_path):
    from synthlab.studio.library import by_family

    client = _client(tmp_path)
    name = by_family()[0][1][0]["name"]
    lib = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/library"}
    r = client.post(
        "/fragment/layer/add", data={"source": "library", "name": name}, headers=lib
    )
    assert r.status_code == 200 and r.headers.get("HX-Push-Url") == "/view/editor"
    assert client.app.state.studio.patch.name == name


def test_clear_empties_a_layer_but_never_the_only_sound(tmp_path):
    client = _client(tmp_path)
    s = _two_layers(client)
    assert _post(client, "/fragment/layer/clear", {"index": "1"}).status_code == 200
    assert s.layers[1]["patch"] is None
    ctx = client.app.state.svc.layer_context()
    assert ctx["strip"][1]["empty"] and not ctx["can_clear_edited"]
    r = _post(client, "/fragment/layer/clear", {"index": "0"})
    assert r.status_code == 422 and "only sound" in r.text and s.patch.name == "lead"
    assert _post(client, "/fragment/layer/clear", {"index": "9"}).status_code == 422


def test_clearing_the_edited_layer_moves_the_panels_to_its_neighbour(tmp_path):
    """The user problem: ✕ on the layer being edited was disabled ("edit
    another layer to clear this one")."""
    client = _client(tmp_path)
    s, svc = _two_layers(client), client.app.state.svc
    assert svc.layer_context()["can_clear_edited"]
    r = _post(client, "/fragment/layer/clear", {"index": "0"})
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    assert s.active_layer == 1 and s.patch.name == "sub"
    assert s.layers[0]["patch"] is None and svc.layer_context()["strip"][0]["empty"]
    assert 'id="l1-clear"' in r.text and s.main_index() == 1


def test_clearing_the_edited_main_lane_leaves_its_notes_with_a_follower(tmp_path):
    client = _client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    s.arrangement = replace(s.arrangement, cells=[["A2", 0], ["C3", 2]])
    _post(client, "/fragment/layer/add")  # layer 2 follows, and is edited
    _post(client, "/fragment/layer/select", {"index": "0"})
    heard = svc.render_inputs("perform").arr
    assert _post(client, "/fragment/layer/clear", {"index": "0"}).status_code == 200
    assert s.active_layer == 1 and s.main_index() == 1 and "follow" not in s.layers[1]
    assert s.arrangement.cells == [["A2", 0], ["C3", 2]]
    inp = svc.render_inputs("perform")
    assert inp.arr == heard and not inp.layers


def test_clearing_the_main_lane_unpins_it(tmp_path):
    client = _client(tmp_path)
    s = _two_layers(client)
    _post(client, "/fragment/layer/select", {"index": "2"})  # a plain empty slot
    assert s.main_layer == 0
    _post(client, "/fragment/layer/clear", {"index": "0"})
    assert s.main_layer is None and s.main_index() == 2


def test_clearing_the_main_lane_leaves_its_notes_with_the_edited_follower(tmp_path):
    """The user problem: add a layer (it follows, its own grid is empty),
    ✕ the layer it came from — and the notes left with it: the loop played
    silence. The follower takes them over and is the main lane."""
    client = _client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    s.arrangement = replace(s.arrangement, cells=[["A2", 0], ["C3", 2]], slides=[2])
    _post(client, "/fragment/layer/add")
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    assert s.active_layer == 1 and s.arrangement.cells == []
    heard = svc.render_inputs("perform").arr
    assert _post(client, "/fragment/layer/clear", {"index": "0"}).status_code == 200
    assert s.layers[0]["patch"] is None and "follow" not in s.layers[1]
    assert s.main_layer == 1 and s.main_index() == 1
    assert s.arrangement.cells == [["A1", 0], ["C2", 2]] and s.arrangement.slides == [2]
    inp = svc.render_inputs("perform")
    assert inp.arr == heard and not inp.layers
    assert np.abs(render_audio(inp)).max() > 0.1


def test_clearing_the_main_lane_promotes_a_follower_that_is_not_edited(tmp_path):
    client = _client(tmp_path)
    s = _two_layers(client)
    s.arrangement = replace(s.arrangement, cells=[["A2", 0]])
    s.layers[2] = {**s.layers[1], "follow": {"octave": 1}}
    _post(client, "/fragment/layer/follow", {"index": "1", "octave": "-1"})
    _post(client, "/fragment/layer/select", {"index": "3"})  # a plain empty slot
    _post(client, "/fragment/layer/clear", {"index": "0"})
    assert s.main_layer == 1 and s.main_index() == 1
    assert s.layers[1]["cells"] == [["A1", 0]] and "follow" not in s.layers[1]
    # the other follower is re-based on the new main: +1 from A2 would be
    # +2 from A1, past the range a follower may transpose, so it stops at +1
    assert s.layers[2]["follow"] == {"octave": 1}


def test_a_sound_with_layers_loaded_into_a_follower_keeps_the_notes(tmp_path):
    client = _client(tmp_path)
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, cells=[["A2", 0]])
    _post(client, "/fragment/layer/add")
    s.load_sound(Patch(name="pad"), {"layers": [], "main_fx": [], "curves": {}})
    assert s.layers[0]["patch"] is None and s.main_index() == 1
    assert s.arrangement.cells == [["A2", 0]]


# --- the strip itself ------------------------------------------------------------


def test_the_strip_marks_editing_apart_from_hearing(tmp_path):
    client = _client(tmp_path)
    _two_layers(client)
    page = client.get("/").text
    assert 'id="layer-strip"' in page
    assert page.count('class="lrow') == 4
    assert 'aria-current="true"' in page and "✎</span> editing" in page
    assert 'aria-label="heard on ▶"' in page
    assert "plays the phrase (no notes of its own)" in page
    # every toggle says its state to assistive tech
    for ident in ("l1-m", "l1-s", "l1-follow", "ls-all", "ls-this"):
        assert f'id="{ident}"' in page
    assert page.count("aria-pressed=") >= 11


def test_clear_asks_no_confirm(tmp_path):
    """The user's ask: ✕ empties the layer at once, no browser popup."""
    client = _client(tmp_path)
    _post(client, "/fragment/layer/add")
    strip = client.get("/").text.split('id="layer-strip"')[1].split("</section>")[0]
    assert 'id="l0-clear"' in strip and "hx-confirm" not in strip
