"""An FX unit's presets (fxrack.FX_PRESETS, a set with `name=recall`): the
unit's ★ menu and its right-click rows set every parameter in one move —
a preset over the defaults, or ↺ the defaults alone: a right-click box of
presets for each effect.

The recall is `fxrack.edit_chain`'s, so every rack has it: the session's
scopes (`/fragment/fx/set`), a Song audio track's (`/song/track/{id}/fx`)
and the vocal booth's (`/voice/booth/fx`), all drawn by the one macro
(fragments/fx_units.html). And an edit of a session rack is a step of the
patch ring: "Undo the last tweak" takes it back."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import booth, voice
from synthlab.studio import song_model as sm
from synthlab.studio.fxrack import (
    FX_PRESETS,
    FX_UNITS,
    RackError,
    clean_chain,
    edit_chain,
    rack_rows,
    unit_id,
    unit_preset,
)
from synthlab.studio.server import create_app
from tests.studio.test_booth import _lab, _take
from tests.studio.test_song_routes import post, song, use_song

ED = {"HX-Current-URL": "http://testserver/view/editor", "Origin": "http://testserver"}
VOICE = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/voice"}
REVERB_DEFAULTS = {"decay": 2.0, "mix": 0.25, "size": 0.8}
CATHEDRAL = {"decay": 5.5, "mix": 0.35, "size": 1.0}


@pytest.mark.parametrize("kind", sorted(FX_PRESETS))
def test_every_preset_is_inside_its_units_own_ranges(kind):
    params = FX_UNITS[kind]["params"]
    assert FX_PRESETS[kind], kind
    for name, preset in FX_PRESETS[kind].items():
        assert preset, (kind, name)
        for p, v in preset.items():
            lo, hi, _ = params[p]  # a KeyError is a parameter the unit lacks
            assert lo <= v <= hi, (kind, name, p, v)
        # just the preset: the recall's clean fills the rest with defaults
        assert unit_preset(kind, name) == preset
        (unit,) = clean_chain([{"kind": kind, "params": unit_preset(kind, name)}])
        assert set(unit["params"]) == set(params)
        assert all(unit["params"][p] == params[p][2] for p in params if p not in preset)


def test_defaults_and_unknowns():
    assert unit_preset("reverb", "defaults") == {}
    assert unit_preset("reverb", "Nope") is None
    assert unit_preset("nope", "defaults") is None


def test_edit_chain_recalls_a_preset_over_the_defaults_and_keeps_the_id():
    chain = [{"kind": "reverb", "params": {"decay": 9.0, "mix": 0.9}}]
    uid = unit_id(chain, 0)  # a lane's unit: the recall keeps it
    out, said, at = edit_chain(
        chain, "set", {"index": "0", "name": "recall", "value": "Cathedral"}
    )
    assert out == [{"kind": "reverb", "params": CATHEDRAL, "id": uid}]
    assert (said, at) == ("Reverb: Cathedral", 0)
    assert chain[0]["params"]["decay"] == 9.0  # the chain handed in is not changed
    out, _said, _at = edit_chain(
        out, "set", {"index": "0", "name": "recall", "value": "defaults"}
    )
    assert out[0]["params"] == REVERB_DEFAULTS
    with pytest.raises(RackError, match="no such preset"):
        edit_chain(out, "set", {"index": "0", "name": "recall", "value": "Nope"})


def test_edit_chain_has_the_step_editor_and_a_gates_rhythms():
    (gate,) = clean_chain([{"kind": "gate", "params": {}}])
    pat = gate["pattern"]
    out, *_ = edit_chain([gate], "set", {"index": "0", "name": "step", "value": "1"})
    assert out[0]["pattern"][1] != pat[1] and out[0]["pattern"][2:] == pat[2:]
    out, *_ = edit_chain(out, "set", {"index": "0", "name": "steps", "value": "32"})
    assert len(out[0]["pattern"]) == 32
    out, *_ = edit_chain(
        out, "set", {"index": "0", "name": "preset", "value": "eighths"}
    )
    assert out[0]["pattern"] == "x.x.x.x.x.x.x.x."
    for bad in (
        {"name": "steps", "value": "12"},
        {"name": "step", "value": "99"},
        {"name": "step", "value": "x"},
        {"name": "preset", "value": "nope"},
    ):
        with pytest.raises(RackError):
            edit_chain(out, "set", {"index": "0", **bad})
    # a rhythm is a gate's alone
    (rev,) = clean_chain([{"kind": "reverb", "params": {}}])
    with pytest.raises(RackError):
        edit_chain([rev], "set", {"index": "0", "name": "preset", "value": "eighths"})


def test_the_rows_carry_what_the_macro_draws():
    chain = [{"kind": "drive", "params": {"mode": 2}}, {"kind": "gate", "params": {}}]
    drive, gate = rack_rows(chain)
    mode = next(p for p in drive["params"] if p["name"] == "mode")
    assert mode["choices"] and mode["value"] == 2.0 and mode["default"] == 0.0
    assert drive["presets"] == list(FX_PRESETS["drive"]) and not drive["rhythms"]
    assert gate["pattern"] and gate["rhythms"] and gate["last"] and drive["first"]


def test_the_route_recalls_a_preset_then_the_defaults(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    r = client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "reverb"}, headers=ED
    )
    assert r.status_code == 200
    # the rack draws the unit's ★ menu and its rows for the right click
    assert (
        'data-menu="Preset: Hall"' in r.text
        and 'data-menu="Defaults: every setting back"' in r.text
    )
    chain = s.layers[s.active_layer]["fx"]
    data = {"scope": "layer", "index": "0", "name": "recall", "value": "Cathedral"}
    assert client.post("/fragment/fx/set", data=data, headers=ED).status_code == 200
    assert chain[0]["params"] == CATHEDRAL
    data["value"] = "defaults"
    assert client.post("/fragment/fx/set", data=data, headers=ED).status_code == 200
    assert chain[0]["params"] == REVERB_DEFAULTS
    data["value"] = "Nope"
    assert client.post("/fragment/fx/set", data=data, headers=ED).status_code == 422


# --- the Song drawer's and the booth's racks: the same macro, their own routes ----


def _drawer(client) -> str:
    r = post(client, "/song/drawer", {"section_id": "s1", "track_id": "ta"})
    assert r.status_code == 200, r.text[:300]
    return r.text.split('id="song-drawer"')[1].split("</fieldset>")[0]


def test_a_song_audio_tracks_rack_recalls_presets_and_undoes_them(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        use_song(client)
        url = "/song/track/ta/fx"
        assert post(client, url, {"op": "add", "kind": "reverb"}).status_code == 200
        rack = _drawer(client)
        # THE rack: ★ presets and defaults, jacks for the leads, the knob's
        # default for a middle click — every post to the track's own route
        assert 'popovertarget="fxp-trk-ta-0"' in rack
        assert 'data-menu="Preset: Cathedral"' in rack
        assert rack.count('class="jack"') == 2 and 'data-default="2.0"' in rack
        assert 'hx-post="/song/track/ta/fx"' in rack and "/fragment/fx/" not in rack
        assert '"name": "recall", "op": "set", "value": "Cathedral"' in rack
        r = post(
            client,
            url,
            {"op": "set", "index": "0", "name": "recall", "value": "Cathedral"},
        )
        assert r.status_code == 200, r.text[:300]
        fx = lambda: sm._track(song(client), "ta")["fx"]  # noqa: E731
        assert fx()[0]["params"] == CATHEDRAL
        # two presses, two steps: a recall never joins the one before it
        post(
            client,
            url,
            {"op": "set", "index": "0", "name": "recall", "value": "defaults"},
        )
        assert fx()[0]["params"] == REVERB_DEFAULTS
        post(client, "/song/undo")
        assert fx()[0]["params"] == CATHEDRAL
        post(client, "/song/undo")
        assert fx()[0]["params"] == REVERB_DEFAULTS  # as added
        refused(
            post(
                client,
                url,
                {"op": "set", "index": "0", "name": "recall", "value": "Nope"},
            )
        )


def refused(r) -> None:
    assert r.status_code == 422, r.text[:300]


@pytest.fixture
def booth_client(tmp_path):
    voice.RESULTS = voice.LRU(4)
    booth.RESULTS = booth.LRU(4)
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def test_the_booths_rack_recalls_presets_and_has_the_step_editor(booth_client):
    client = booth_client
    _take(client)
    r = client.post(
        "/voice/booth/fx", data={"op": "add", "kind": "plate"}, headers=VOICE
    )
    assert r.status_code == 200
    rack = r.text.split('class="fx-scope vb-rack"')[1].split("</fieldset>")[0]
    assert 'popovertarget="fxp-booth-0"' in rack and 'data-menu="Preset: Vocal"' in rack
    assert rack.count('class="jack"') == 2
    r = client.post(
        "/voice/booth/fx",
        data={"op": "set", "index": "0", "name": "recall", "value": "Long"},
        headers=VOICE,
    )
    assert r.status_code == 200
    (unit,) = _lab(client)["booth"]["chain"]
    assert unit["params"]["decay"] == 5.0
    assert unit["params"]["predelay"] == 0.03
    # a gate: its steps, its length and its rhythms, which the booth lacked
    r = client.post(
        "/voice/booth/fx", data={"op": "add", "kind": "gate"}, headers=VOICE
    )
    assert 'class="fx-steps"' in r.text and "rhythm…" in r.text
    r = client.post(
        "/voice/booth/fx",
        data={"op": "set", "index": "1", "name": "preset", "value": "eighths"},
        headers=VOICE,
    )
    assert r.status_code == 200
    assert _lab(client)["booth"]["chain"][1]["pattern"] == "x.x.x.x.x.x.x.x."


# --- undo: a session rack's edit is a step of the patch ring ------------------------


def _set(client, **data):
    r = client.post(
        "/fragment/fx/set", data={k: str(v) for k, v in data.items()}, headers=ED
    )
    assert r.status_code == 200, r.text[:200]


def test_undo_takes_back_rack_edits_one_gesture_at_a_time(tmp_path, monkeypatch):
    from synthlab.studio import state as state_mod

    now = [100.0]
    monkeypatch.setattr(state_mod, "monotonic", lambda: now[0])
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    layer = lambda: s.layers[s.active_layer]["fx"]  # noqa: E731
    undo = lambda: client.post("/fragment/undo", headers=ED)  # noqa: E731
    client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "reverb"}, headers=ED
    )
    client.post(
        "/fragment/fx/add", data={"scope": "master", "kind": "width"}, headers=ED
    )
    # a knob's drag: posts inside a second of each other, one step
    for v in (0.3, 0.4, 0.5):
        now[0] += 0.2
        _set(client, scope="layer", index=0, name="mix", value=v)
    now[0] += 5
    _set(client, scope="layer", index=0, name="recall", value="Cathedral")
    now[0] += 0.1  # a press right after: its own step all the same
    _set(client, scope="layer", index=0, name="recall", value="defaults")
    assert layer()[0]["params"] == REVERB_DEFAULTS
    assert undo().status_code == 200
    assert layer()[0]["params"] == CATHEDRAL
    undo()
    assert layer()[0]["params"]["mix"] == 0.5
    undo()  # the whole drag
    assert layer()[0]["params"]["mix"] == REVERB_DEFAULTS["mix"]
    assert [u["kind"] for u in s.fx_master] == ["width"]
    undo()  # the master's unit
    assert s.fx_master == []
    undo()  # the layer's
    assert layer() == []
    assert undo().status_code == 204  # nothing left


def test_a_patch_tweak_after_a_knob_is_its_own_step(tmp_path, monkeypatch):
    from synthlab.studio import state as state_mod

    now = [100.0]
    monkeypatch.setattr(state_mod, "monotonic", lambda: now[0])
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "reverb"}, headers=ED
    )
    now[0] += 5
    _set(client, scope="layer", index=0, name="mix", value=0.6)
    drive = s.patch.drive
    now[0] += 0.3  # inside the drag's second, but the patch: a step of its own
    s.push_history()
    s.patch = replace(s.patch, drive=drive + 1)
    client.post("/fragment/undo", headers=ED)
    assert s.patch.drive == drive and s.layers[0]["fx"][0]["params"]["mix"] == 0.6
    client.post("/fragment/undo", headers=ED)
    assert s.layers[0]["fx"][0]["params"]["mix"] == REVERB_DEFAULTS["mix"]


def test_a_drum_bus_step_is_skipped_outside_the_edit_it_was_taken_in(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    before = [{"kind": "reverb", "params": dict(REVERB_DEFAULTS)}]
    s.fx_drums[:] = clean_chain(before)
    s.song_edit_track = "td"  # as if the drums track's kit were on Drums
    s.fx_drums[:] = []
    s.push_fx_undo("drums", clean_chain(before))
    s.song_edit_track = None  # the edit ended: the loop's bus is back
    s.fx_drums[:] = []
    assert s.pop_history() is False and s.fx_drums == []
