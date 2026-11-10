"""Following layers: a layer that plays the main lane's notes at an octave.

A bred Sound's layers follow (`{"follow": {"octave": -1}}`), so the card,
"Use with its layers" and any later edit of the main grid play the same
stack. Resolved at render time (`arrange.follow_layer`), so the stem key
carries the notes it plays; a layer switch keeps it following (the main
lane is pinned), and the song bridge turns one into a plain slot with
those notes (`state.materialise`), sounding the same.
"""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio import schema
from synthlab.studio.arrange import follow_layer
from synthlab.studio.render import (
    RenderInputs,
    StemCache,
    read_pcm16,
    render_audio,
    render_stems,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import (
    Arrangement,
    StudioState,
    _empty_layer,
    preset_from_json,
)
from synthlab.synth import Patch, preset

SUB = Patch(name="sub", osc1="sine", cutoff=300.0).to_json()


def follower(octave=-1, **kw) -> dict:
    return {
        "patch": SUB,
        "cells": [],
        "muted": False,
        "gain": 0.7,
        "fx": [],
        "follow": {"octave": octave},
        "role": "sub",
        **kw,
    }


ARR = Arrangement(cells=[["A2", 0], ["C3", 2], ["E3", 3]], slides=[2])


def test_follow_plays_the_main_lanes_notes_an_octave_away():
    plain = follow_layer(follower(-1), ARR)
    assert "follow" not in plain
    assert plain["cells"] == [["A1", 0], ["C2", 2], ["E2", 3]]
    assert plain["slides"] == [2]  # the main lane's sequencing too
    assert "velocities" not in plain  # absent: the main lane's
    up = follow_layer(follower(1), ARR)
    assert up["cells"] == [["A3", 0], ["C4", 2], ["E4", 3]]
    # a plain layer is untouched
    lay = {**follower(), "cells": [["A1", 0]]}
    del lay["follow"]
    assert follow_layer(lay, ARR) is lay


def test_a_follower_renders_as_the_plain_layer_it_resolves_to():
    p = preset("pad_warm")
    lay = follower(-1)
    as_plain = follow_layer(lay, ARR)
    a = render_audio(RenderInputs(patch=p, arr=ARR, layers=[lay], parts=("synth",)))
    b = render_audio(
        RenderInputs(patch=p, arr=ARR, layers=[as_plain], parts=("synth",))
    )
    np.testing.assert_array_equal(a, b)
    alone = render_audio(RenderInputs(patch=p, arr=ARR, parts=("synth",)))
    assert not np.array_equal(a, alone)


def test_the_stem_key_follows_the_main_grid():
    """Edit the main lane's notes and the follower's stem re-renders: its
    cache payload holds the notes it plays, not just `follow`."""
    p = preset("pad_warm")
    cache = StemCache()
    lay = follower(-1)
    inp = RenderInputs(patch=p, arr=ARR, layers=[lay], parts=("synth",))
    first = dict(render_stems(inp, cache=cache))
    moved = replace(ARR, cells=[["A2", 0], ["D3", 1]])
    inp2 = replace(inp, arr=moved)
    assert inp2.key() != inp.key()
    second = dict(render_stems(inp2, cache=cache))
    assert not np.array_equal(first["layer2"], second["layer2"])
    fresh = dict(render_stems(inp2))  # no cache: what it must be
    np.testing.assert_array_equal(second["layer2"], fresh["layer2"])
    # another octave is another sound, and another key
    other = replace(inp, layers=[follower(-2)])
    assert other.key() != inp.key()


def test_schema_keeps_follow_and_role_and_adds_neither():
    n = schema.nodes()
    clean = schema.conform(follower(-1), n.LAYERS_OTHER.elem)
    assert clean["follow"] == {"octave": -1} and clean["role"] == "sub"
    wild = schema.conform(
        {**follower(), "follow": {"octave": 9}, "role": "kazoo"}, n.LAYERS_OTHER.elem
    )
    assert wild["follow"] == {"octave": 1} and "role" not in wild
    # an old lane (preset, scene, state slot) gains no key
    old = {"patch": SUB, "cells": [["A1", 0]], "muted": False, "gain": 1.0, "fx": []}
    assert set(schema.conform(old, n.LAYERS_OTHER.elem)) == set(old)
    slot = schema.conform(_empty_layer(), n.LAYER)
    assert set(slot) == set(_empty_layer())
    assert schema.conform(follower(), n.LAYER)["follow"] == {"octave": -1}


def test_old_presets_load_unchanged():
    """A format-2 preset written before following layers loads byte for
    byte as it did: no layer gains `follow` or `role`, so its render key is
    the one it always had."""
    raw = {
        "format": 2,
        "patch": Patch(name="old").to_json(),
        "layers": [
            {"patch": SUB, "cells": [["A1", 0]], "muted": False, "gain": 1.0, "fx": []}
        ],
        "main_fx": [],
        "curves": {},
    }
    patch, chain = preset_from_json(json.loads(json.dumps(raw)))
    assert chain["layers"] == raw["layers"]


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _loop_with_follower(s: StudioState) -> None:
    s.arrangement = replace(ARR, velocities={"0": 0.5, "2": 1.0})
    s.layers[1] = {**_empty_layer(), **follower(-1)}
    s.save()


def test_v2_preset_round_trips_a_follower(tmp_path):
    client = _client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    _loop_with_follower(s)
    before = svc.render("editor").wav
    client.post("/fragment/preset/save", data={"name": "stack", "include_layers": "on"})
    saved = json.loads((s.preset_dir() / "stack.json").read_text(encoding="utf-8"))
    assert saved["layers"][0]["follow"] == {"octave": -1}
    # a fresh session loading it plays the same loop
    other = _client(tmp_path / "other")
    o = other.app.state.studio
    o.arrangement = s.arrangement
    (o.preset_dir() / "stack.json").write_text(json.dumps(saved), encoding="utf-8")
    other.post("/fragment/preset/load", data={"name": "stack"})
    assert other.app.state.svc.render("editor").wav == before


def test_switching_layers_keeps_followers_following_and_the_loop(tmp_path):
    """Editing a follower keeps it following: the main lane stays where it
    was (pinned), the edited follower plays the main lane's notes at its
    octave, and its own (empty) cells are left alone for Follow off."""
    client = _client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    _loop_with_follower(s)
    s.audition = "riff"
    loop = svc.render("perform").wav
    bench = svc.render("editor").wav
    s.select_layer(1)  # the follower becomes the lane being edited
    assert s.main_layer == 0 and s.main_index() == 0 and s.follows(1)
    assert s.arrangement.cells == []  # its own notes: none, kept
    assert s.lane_arrangement().cells == [["A1", 0], ["C2", 2], ["E2", 3]]
    np.testing.assert_allclose(svc.render("perform").audio(), _audio(loop), atol=1e-6)
    # Synths: the stack on the phrase either way
    np.testing.assert_allclose(svc.render("editor").audio(), _audio(bench), atol=1e-6)
    s.select_layer(0)
    assert s.layers[1]["follow"] == {"octave": -1}
    assert svc.render("perform").wav == loop
    assert svc.render("editor").wav == bench


def _audio(wav: bytes) -> np.ndarray:
    return read_pcm16(wav)[0]


def test_a_follower_goes_into_the_song_as_its_notes(tmp_path):
    s = StudioState(tmp_path)
    _loop_with_follower(s)
    sid = s.loop_to_section("A")
    sec = s.song["sections"][0]
    assert sec["id"] == sid
    pats = [s.song["patterns"][pid] for pid in sec["cells"].values() if pid]
    cells = [p["cells"] for p in pats if p["kind"] == "notes"]
    assert [["A1", 0], ["C2", 2], ["E2", 3]] in cells
    # and back: a plain layer, the same notes
    s.section_to_loop(sid)
    lanes = [lay for lay in s.layers if lay.get("cells")]
    assert any(lay["cells"] == [["A1", 0], ["C2", 2], ["E2", 3]] for lay in lanes)
    assert not any("follow" in lay for lay in s.layers)
