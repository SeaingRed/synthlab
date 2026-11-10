"""The first save of a session is in the schema's conformed form.

The rule (`Persistence._payload`): **what is written is what a load of it
would hold**. So, for a session built through any path that puts things in
the session — a Sound with layers, `add_layer`, "before last load", the FX
rack, a breed card, the sound agent's tools, a Sample Lab region, the song
bridges, a reset, a project import — these hold *from the first save*:

- `save -> load -> save` is a fixed point: the bytes of state.json do not
  change on a restart with no edit (they used to: a layer a Sound brought
  had no `silent` / `seq` / `curves`, a slot's `follow` sat after its other
  keys, and "before last load" kept copies of both);
- the saved bytes are their own conformed form;
- every view's render key, the song's WAV URLs and every scope's "changed"
  flag are the same before the restart and after it: the live session was
  never unconformed in a way a render or a comparison can see.

The legacy half: a state.json written *before* this (the first save of the
rich session of `test_state_split.py`, kept as
`tests/fixtures/state_split/legacy_first_save*.json`) still loads, and is
written in conformed form by the first save after — once.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import schema
from synthlab.studio.factory import FACTORY
from synthlab.studio.fxrack import CHAIN_PRESETS
from synthlab.studio.library import LIBRARY
from synthlab.studio.recipes import RECIPES
from synthlab.studio.server import create_app
from synthlab.studio.state import RESET_SCOPES, StudioState
from synthlab.studio.views import VIEW_NAMES
from synthlab.synth import Patch
from tests.studio.lab_helpers import LAB, song_wav, source_id, upload

FIXTURES = Path(__file__).parent.parent / "fixtures" / "state_split"
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _post(client, path: str, **data):
    r = client.post(path, data=data, headers=HX)
    assert r.status_code < 400, (path, r.status_code, r.text[:200])
    return r


def _recipe_names() -> list[str]:
    return [r if isinstance(r, str) else r["name"] for r in RECIPES]


# --- the paths: each leaves a session only that path could have made ------------


def a_sound_with_layers_then_another_then_back(c, svc, s):
    """`load_sound` with a chain: the Sound's layers land in the slots, the
    stack it replaced goes to "before last load", and that is swapped back."""
    for name in _recipe_names():
        _post(c, "/fragment/recipe/load", name=name)
    _post(c, "/fragment/restore_prev")


def a_saved_stack_loaded_back(c, svc, s):
    """`load_preset("stack")`: a format-2 preset's layers."""
    _post(c, "/fragment/layer/add")
    _post(c, "/fragment/layer/select", index="0")
    _post(c, "/fragment/fx/add", scope="layer", kind="reverb")
    _post(c, "/fragment/preset/save", name="stack", include_layers="on")
    _post(c, "/fragment/factory/load", name=FACTORY[0]["name"])
    _post(c, "/fragment/preset/load", name="stack")


def layers_added_and_toggled(c, svc, s):
    """`add_layer` and the strip: follow off and on, solo, level — each
    writes a key into a slot's dict after the others."""
    _post(c, "/fragment/layer/add")
    _post(c, "/fragment/layer/add", source="library", name=LIBRARY[0]["name"])
    _post(c, "/fragment/layer/follow", index="1", on="0")
    _post(c, "/fragment/layer/follow", index="1", on="1", octave="1")
    _post(c, "/fragment/layer/solo", index="2")
    _post(c, "/fragment/layer/gain", index="1", gain="0.5")
    _post(c, "/fragment/layer/select", index="0")
    _post(c, "/fragment/layer/clear", index="2")


def a_layer_added_with_a_hand_built_fx_unit(c, svc, s):
    """`add_layer` given a chain whose unit names one parameter: the rack's
    form (every parameter) is what the slot holds, and what the key hashes."""
    reverb = [{"kind": "reverb", "params": {"mix": 0.3}}, {"kind": "nope"}]
    i = s.add_layer(Patch(name="sub", osc1="sine"), reverb, octave=-1, gain=0.8)
    assert set(s.layers[i]["fx"][0]["params"]) == {"decay", "mix", "size"}
    assert len(s.layers[i]["fx"]) == 1
    s.stash_for_load()
    s.patch = replace(s.patch, resonance=0.4, name="sub 2")
    s.select_layer(0)
    s.save()


def the_fx_rack(c, svc, s):
    _post(c, "/fragment/fx/chain", scope="master", chain=next(iter(CHAIN_PRESETS)))
    _post(c, "/fragment/fx/add", scope="layer", kind="delay")
    _post(c, "/fragment/fx/add", scope="drums", kind="pump")
    _post(c, "/fragment/fx/set", scope="layer", index="0", name="mix", value="0.4")
    _post(c, "/fragment/fx/move", scope="master", index="0", dir="1")


def a_bred_sound_with_its_layers(c, svc, s):
    """A layered breed card used whole, then another one's main lane only."""
    _post(c, "/fragment/breed/depth", depth="deep")
    _post(c, "/fragment/breed/generation")
    _post(c, "/fragment/breed/adopt", id=s.breed_tiles[0]["id"])
    _post(c, "/fragment/breed/generation")
    _post(c, "/fragment/breed/adopt", id=s.breed_tiles[1]["id"], main="1")


def the_sound_agents_tools(c, svc, s):
    """The agent's tool edits, and what the end of its turn stashes (the
    stack the turn replaced: `AgentService`'s own lines)."""
    from synthlab.studio.agent import context as ctx
    from synthlab.studio.agent import tools

    snap = ctx.snapshot(s)
    tools.t_add_layer(s, svc, {"role": "sub"})
    tools.t_add_layer(s, svc, {"from_sound": LIBRARY[0]["name"], "level_db": -9})
    units = [{"kind": "delay", "params": {"mix": 0.2}}]
    tools.t_set_fx_chain(s, svc, {"scope": "layer", "units": units})
    tools.t_set_fx_chain(s, svc, {"scope": "master", "units": [{"kind": "reverb"}]})
    tools.t_draw_curve(s, svc, {"param": "cutoff", "shape": "rise"})
    s.push_undo_step(snap["patch"])
    s.slot_prev = Patch.from_dict(snap["patch"])
    s.stack_prev = {
        "layers": snap["layers"],
        "main_fx": snap["layers"][s.active_layer].get("fx") or [],
        "curves": snap["curves"],
    }
    s.save()
    # ... and its undo, which puts a snapshot back whole
    ctx.restore(s, ctx.snapshot(s))
    s.save()


def a_lab_region_as_a_sampler_sound_and_a_drum_pattern(c, svc, s):
    """The Sample Lab's `adopt_audio` and its pattern onto the drum grid."""
    assert upload(c, song_wav(bars=2)).status_code == 200
    form = {"source": source_id(c)}
    region = {"mode": "bars", "bar": "1", "bars": "1"}
    assert c.post("/lab/region", data=form | region, headers=LAB).status_code == 200
    r = c.post("/lab/send", data=form | {"target": "sampler"}, headers=LAB)
    assert r.status_code == 200 and s.patch.osc1 == "sampler"
    c.post("/lab/drums", data=form | {"to": "grid"}, headers=LAB)


def the_song_bridges(c, svc, s):
    """Loop -> section twice, a section back into the loop (its lanes into
    the slots), a track's sound opened and closed."""
    _post(c, "/fragment/layer/add")
    _post(c, "/fragment/layer/select", index="0")
    verse = s.loop_to_section("Verse")
    s.arrangement = replace(s.arrangement, cells=[["A1", 1], ["E2", 3]])
    s.loop_to_section("Chorus")
    s.save()
    assert s.section_to_loop(verse)
    s.save()
    synth = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
    _post(c, f"/song/track/{synth}/edit-sound")
    _post(c, "/fragment/fx/add", scope="layer", kind="delay")
    s.mirror_patch_to_track()
    assert s.end_track_edit()
    s.save()


def reverts_and_resets(c, svc, s):
    _post(c, "/fragment/recipe/load", name=_recipe_names()[0])
    _post(c, "/fragment/fx/add", scope="layer", kind="delay")
    _post(c, "/fragment/reset", scope="fx", mode="revert")
    _post(c, "/fragment/reset", scope="sound", mode="defaults")
    _post(c, "/fragment/reset", scope="all", mode="defaults")


def an_imported_project_written_by_hand(c, svc, s):
    """An import conforms as a load does; what it leaves is saved at once."""
    project = json.loads(c.get("/project.json").content)
    project["fx_master"] = [{"kind": "delay", "params": {"mix": 0.2}}]
    project["layers"][1] = {"patch": Patch(name="x").to_json(), "follow": {"octave": 1}}
    project["stack_prev"] = {"layers": [{"fx": [{"kind": "reverb"}]}], "main_fx": []}
    del project["baselines"], project["macros"]
    body = json.dumps(project).encode()
    r = c.post("/project/import", files={"file": ("p.json", body, "application/json")})
    assert r.status_code < 400, r.text[:200]


PATHS = [
    a_sound_with_layers_then_another_then_back,
    a_saved_stack_loaded_back,
    layers_added_and_toggled,
    a_layer_added_with_a_hand_built_fx_unit,
    the_fx_rack,
    a_bred_sound_with_its_layers,
    the_sound_agents_tools,
    a_lab_region_as_a_sampler_sound_and_a_drum_pattern,
    the_song_bridges,
    reverts_and_resets,
    an_imported_project_written_by_hand,
]


# --- the invariant ----------------------------------------------------------------


def _seen(client) -> dict:
    """Everything a render or a comparison reads off the live session: each
    view's loop key, each scope's "changed" flag, and — when the song has
    sections — the WAV URLs of its plan (they carry the section keys)."""
    svc = client.app.state.svc
    s: StudioState = svc.state
    out: dict = {
        "keys": {v: svc.render_inputs(v).key() for v in VIEW_NAMES},
        "dirty": {scope: s.is_dirty(scope) for scope in RESET_SCOPES},
    }
    if s.song["sections"]:
        plan = client.get("/song/plan.json").json()
        out["song"] = [(sec["url"], sec["loop_url"]) for sec in plan["sections"]]
    return out


def _conformed(saved: bytes) -> str:
    data = json.loads(saved)
    clean = schema.conform_strict(data, schema.nodes().PAYLOAD)
    return json.dumps({"v": data["v"], **clean}, indent=2)


@pytest.mark.parametrize("path", PATHS, ids=lambda f: f.__name__)
def test_the_first_save_is_a_fixed_point_and_a_reload_moves_no_key(tmp_path, path):
    state_json = tmp_path / "state.json"
    with TestClient(create_app(data_dir=tmp_path)) as c:
        svc = c.app.state.svc
        path(c, svc, svc.state)
        svc.state.save()  # the FIRST save of what the path left
        first = state_json.read_bytes()
        before = _seen(c)
        # nothing a reader sees is the save's doing (it must not touch the
        # live session), and a second save writes the same bytes
        svc.state.save()
        assert state_json.read_bytes() == first
        assert _seen(c) == before
    assert state_json.read_bytes() == first  # the shutdown flush wrote nothing new
    # what was written is its own conformed form, key order included
    assert first.decode("utf-8").replace("\r\n", "\n") == _conformed(first)
    for _restart in range(2):
        with TestClient(create_app(data_dir=tmp_path)) as c:
            assert _seen(c) == before
            c.app.state.svc.state.save()
            assert state_json.read_bytes() == first
        assert state_json.read_bytes() == first


def test_a_session_saved_mid_track_edit_is_a_fixed_point_too(tmp_path):
    """Mid edit the file keeps the loop's own sound (`_payload`): those
    pieces are conformed like the rest. A reload ends the edit, so the keys
    are another session's and are not compared here."""
    state_json = tmp_path / "state.json"
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s: StudioState = c.app.state.svc.state
        _post(c, "/fragment/recipe/load", name=_recipe_names()[0])
        _post(c, "/fragment/layer/add")
        s.loop_to_section("Verse")
        synth = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
        assert s.edit_track(synth)
        s.patch = replace(s.patch, cutoff=5000.0, name="track sound")
        assert s.mirror_patch_to_track()
        s.save()
        first = state_json.read_bytes()
        assert first.decode("utf-8").replace("\r\n", "\n") == _conformed(first)
    for _restart in range(2):
        s = StudioState(tmp_path)
        s.save()
        assert state_json.read_bytes() == first


def test_saving_never_changes_the_live_session(tmp_path):
    """The rule is about the file. The session in memory is left exactly as
    it was — same objects, same dicts, same key order — so no render key of
    a running session moves because it was saved."""
    s = StudioState(tmp_path)
    s.add_layer(Patch(name="sub"), octave=-1)
    s.select_layer(0)
    s.set_layer_solo(1, True)
    lay = s.layers[1]
    order, layers, song, fx = list(lay), s.layers, s.song, s.fx_master
    s.save()
    assert s.layers is layers and s.layers[1] is lay and list(lay) == order
    assert s.song is song and s.fx_master is fx
    assert list(lay)[-2:] == ["follow", "solo"]  # not the schema's order ...
    saved = json.loads(s._state_path.read_text(encoding="utf-8"))
    assert list(saved["layers"][1]).index("follow") < list(saved["layers"][1]).index(
        "silent"
    )  # ... which is what the file holds


def test_a_session_the_schema_refuses_is_still_written(tmp_path, caplog, monkeypatch):
    """Conforming must never cost a save: if the schema raises (it does not
    for any session this suite can build: a bad value takes its default),
    the session is written as it stands, as it was before."""
    s = StudioState(tmp_path)
    s.add_layer(Patch(name="sub"), octave=-1)
    s.select_layer(0)
    live = json.loads(json.dumps(s._live_payload()))

    def refuse(value, node):
        raise schema.Invalid("no")

    monkeypatch.setattr(schema, "conform_strict", refuse)
    with caplog.at_level("ERROR", logger="synthlab.studio.state"):
        s.save()
    assert "not in a savable form" in caplog.text
    assert json.loads(s._state_path.read_text(encoding="utf-8")) == live


# --- files written before this -------------------------------------------------------


def _lf(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


@pytest.mark.parametrize("name", ["rich_state", "rich_state_mid_edit"])
def test_a_file_saved_unconformed_loads_and_is_written_conformed_once(tmp_path, name):
    """`legacy_first_save*.json` is the rich session's first save as it was
    written before `_payload` conformed (a hand-built FX unit without its
    defaults, a preset's layers without `silent` / `seq` / `curves`,
    `follow` after them in "before last load"). It loads as it always did;
    the first save after is the conformed form — the bytes a new session's
    first save is pinned to — and from there nothing moves."""
    suffix = name.removeprefix("rich_state")
    legacy = _lf((FIXTURES / f"legacy_first_save{suffix}.json").read_bytes())
    conformed = _lf((FIXTURES / f"{name}.json").read_bytes())
    assert legacy != conformed
    assert _conformed(legacy).encode() == conformed
    (tmp_path / "state.json").write_bytes(legacy)
    s = StudioState(tmp_path)
    assert _lf(s._state_path.read_bytes()) == legacy  # loading writes nothing
    s.save()
    assert _lf(s._state_path.read_bytes()) == conformed
    again = StudioState(tmp_path)
    again.save()
    assert _lf(again._state_path.read_bytes()) == conformed
    assert not (tmp_path / "state.json.v2.bak").exists()  # not a migration
    assert not list(tmp_path.glob("state.json.corrupt-*"))
