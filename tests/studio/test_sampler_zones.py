"""A multi-sample instrument in the studio: clips as zones of
the sampler (`/fragment/sampler/zone/add`, the drawer's and the hub's ＋
zone), auto-map (`/fragment/sampler/zones/map`, a drawer group's ⤷ zones /
⤷ keys), a zone's edits and the map's tools, undo, the zone map's drawing,
persistence (state.json, a format-2 preset, /project.zip) and the render
keys (studio/samplerview.py, routes/sampler.py; the engine is
tests/test_sampler.py's, the live voice tests/studio/test_live_sampler.py's)."""

from __future__ import annotations

import io
import zipfile

import numpy as np
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.core import SR
from synthlab.studio import samplerview as sv
from synthlab.studio.clips import save_clip
from synthlab.studio.render import patch_key
from synthlab.studio.server import create_app
from synthlab.synth import Patch

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _hx(view: str) -> dict:
    return {**HX, "HX-Current-URL": f"http://testserver/view/{view}"}


def _tone(hz: float, seconds: float = 0.5, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = np.sin(2 * np.pi * hz * t) * amp
    return np.column_stack([x, x])


def _clips(tmp_path) -> None:
    save_clip(tmp_path, "a2", _tone(110))
    save_clip(tmp_path, "a3", _tone(220))
    save_clip(tmp_path, "a4", _tone(440))
    save_clip(tmp_path, "a3 again", _tone(220, amp=0.3))
    save_clip(tmp_path, "hiss", np.random.default_rng(1).normal(0, 0.2, (SR // 2, 2)))


def _map(client, names, mode="pitch", view="editor"):
    data = {"names": "\n".join(names), "mode": mode, "label": "my keys"}
    return client.post("/fragment/sampler/zones/map", data=data, headers=_hx(view))


def test_auto_map_by_pitch_spreads_them_and_makes_a_shared_pitch_a_take(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    r = _map(client, ["a4", "a2", "a3", "a3 again"], view="perform")
    assert r.status_code == 200 and r.headers["HX-Push-Url"] == "/view/editor"
    assert "4 zones mapped by pitch" in r.headers["HX-Trigger"]
    zones = sv.zones_of(s.patch)
    got = sorted((z.root, z.lo, z.hi, z.rr) for z in zones)
    # A2 | A3 (and its second take) | A4, split half way between the roots
    assert got == [(45, 0, 51, 0), (57, 52, 63, 0), (57, 52, 63, 1), (69, 64, 127, 0)]
    assert s.patch.name == "my keys" and s.patch.osc1 == "sampler"
    # every zone's audio is in the sample store, by content
    for z in zones:
        assert (tmp_path / "samples" / f"{z.sample}.wav").is_file()
    # the page draws the map: a box and a row per zone, no waveform
    assert r.text.count('class="zone-row') == 4 and 'id="zone-map"' in r.text
    assert "sampler-svg" not in r.text
    # it plays each key from its own zone
    audio = client.app.state.svc.render("editor").audio()
    assert np.abs(audio).max() > 0.05
    # one undo step back to the sound before
    client.post("/fragment/undo", headers=HX)
    assert s.patch.osc1 == "saw" and not s.patch.sampler_zones


def test_auto_map_a_key_each_is_a_kit(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    _map(client, ["hiss", "a3", "a2"], mode="keys")
    zones = sv.zones_of(client.app.state.studio.patch)
    assert [(z.lo, z.hi, z.root) for z in zones] == [
        (60, 60, 60),
        (61, 61, 61),
        (62, 62, 62),
    ]
    # bad input: a mode it does not know, clips it does not have
    assert _map(client, ["a2"], mode="drums").status_code == 422
    r = _map(client, ["nope", ""])
    assert r.status_code == 422 and "None of those clips" in r.text


def test_a_clip_as_a_zone_joins_the_sampler_or_starts_one(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    add = lambda name, view="editor": client.post(  # noqa: E731
        "/fragment/sampler/zone/add", data={"name": name}, headers=_hx(view)
    )
    # a synth: the clip becomes the sampler's first zone (the whole keyboard)
    r = add("a3", "clips")
    assert r.status_code == 200 and r.headers["HX-Push-Url"] == "/view/editor"
    (z,) = sv.zones_of(s.patch)
    assert (z.lo, z.hi, z.root) == (0, 127, 57) and s.patch.sampler_sample == z.sample
    # the next lands at its pitch and takes the keys above the midpoint
    r = add("a4")
    assert (
        "zone 2: E4" in r.headers["HX-Trigger"] and "root A4" in r.headers["HX-Trigger"]
    )
    assert [(z.lo, z.hi) for z in sv.zones_of(s.patch)] == [(0, 63), (64, 127)]
    # the same pitch again: that zone's next round-robin take
    add("a3 again")
    z = sv.zones_of(s.patch)[-1]
    assert (z.lo, z.hi, z.rr) == (0, 63, 1)
    # a single-sample sampler keeps its sample as zone 1
    client.post("/fragment/sampler/clip", data={"name": "a2"}, headers=HX)
    assert not s.patch.sampler_zones
    add("a4")
    zones = sv.zones_of(s.patch)
    assert [z.root for z in zones] == [45, 69] and zones[
        0
    ].sample == s.patch.sampler_sample
    # one undo step each
    client.post("/fragment/undo", headers=HX)
    assert not s.patch.sampler_zones
    # silence and a missing clip are toasts
    save_clip(tmp_path, "quiet", np.zeros((4410, 2)))
    for name in ("quiet", "nope"):
        r = add(name)
        assert r.status_code == 422 and "nothing to add" in r.text


def test_a_zone_edit_and_the_map_tools(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    _map(client, ["a2", "a3", "a4"])
    edit = lambda **d: client.post("/fragment/sampler/zone", data=d, headers=HX)  # noqa: E731
    # the map's drag: keys and velocities at once, clamped
    r = edit(index="1", action="set", lo="50", hi="70", vlo="200", vhi="-4")
    assert r.status_code == 200
    z = sv.zones_of(s.patch)[1]
    assert (z.lo, z.hi, z.vlo, z.vhi) == (50, 70, 0, 127)
    # an edge set past the other stops there
    edit(index="1", action="set", lo="90")
    z = sv.zones_of(s.patch)[1]
    assert (z.lo, z.hi) == (70, 70)
    # a row's steps
    edit(index="1", action="set", root="58", rr="1", gain="-3", tune="15")
    z = sv.zones_of(s.patch)[1]
    assert (z.root, z.rr, z.gain, z.tune) == (58, 1, -3.0, 15.0)
    # a row's − / + steps from where the zone stands: two presses, two steps
    for _ in range(2):
        edit(index="1", action="step", field="gain", by="1")
    edit(index="1", action="step", field="rr", by="-5")  # no take under 1
    z = sv.zones_of(s.patch)[1]
    assert (z.gain, z.rr) == (-1.0, 0)
    assert edit(index="1", action="step", field="name", by="1").status_code == 422
    edit(index="1", action="detect")
    assert sv.zones_of(s.patch)[1].root == 57
    edit(index="0", action="move", dk="200", dv="0")  # moved as far as it goes
    z = sv.zones_of(s.patch)[0]
    assert z.hi == 127 and z.hi - z.lo == 51
    # refused: an action it does not know, junk numbers
    assert edit(index="0", action="zap").status_code == 422
    assert edit(index="0", action="set", lo="nan").status_code == 422
    assert edit(index="x", action="set", lo="1").status_code == 422
    before = s.patch.sampler_zones
    edit(index="9", action="remove")  # no such zone: nothing moves
    assert s.patch.sampler_zones == before
    # undo walks back one edit at a time
    client.post("/fragment/undo", headers=HX)
    assert sv.zones_of(s.patch)[0].lo == 0
    # spread lays each layer out again by its roots
    client.post("/fragment/sampler/zones", data={"action": "spread"}, headers=HX)
    assert [(z.lo, z.hi) for z in sv.zones_of(s.patch)] == [
        (0, 51),
        (52, 63),
        (64, 127),
    ]
    # a later take is a layer of its own: spread gives it the whole keyboard
    edit(index="1", action="step", field="rr", by="1")
    client.post("/fragment/sampler/zones", data={"action": "spread"}, headers=HX)
    assert [(z.lo, z.hi) for z in sv.zones_of(s.patch)] == [
        (0, 57),
        (0, 127),
        (58, 127),
    ]
    edit(index="0", action="remove")
    assert len(sv.zones_of(s.patch)) == 2
    client.post("/fragment/sampler/zones", data={"action": "clear"}, headers=HX)
    assert not s.patch.sampler_zones and sampler.valid_id(s.patch.sampler_sample)
    r = client.post("/fragment/sampler/zones", data={"action": "nope"}, headers=HX)
    assert r.status_code == 422


def test_the_chops_become_zones(tmp_path):
    save_clip(tmp_path, "a3", _tone(220, 1.0))
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    client.post("/fragment/sampler/clip", data={"name": "a3"}, headers=HX)
    r = client.post("/fragment/sampler/zones", data={"action": "chops"}, headers=HX)
    assert "Chop the sample first" in r.headers["HX-Trigger"]
    client.post("/fragment/sampler/chop", data={"mode": "8"}, headers=HX)
    client.post("/fragment/sampler/zones", data={"action": "chops"}, headers=HX)
    zones = sv.zones_of(s.patch)
    root = int(s.patch.sampler_root)
    assert [(z.lo, z.hi) for z in zones] == [(root + i, root + i) for i in range(8)]
    assert zones[3].start == sampler.parse_slices(s.patch.sampler_slices)[3].a


def test_the_map_keys_saves_and_bundles_every_zone(tmp_path):
    _clips(tmp_path)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        _map(client, ["a2", "a3", "a4"])
        saved = client.app.state.studio.patch
        zones = sv.zones_of(saved)
        # the render key carries the map, so every sample's content
        assert patch_key(saved) != patch_key(
            Patch(**{**saved.to_json(), "sampler_zones": ""})
        )
        project = client.get("/project.json").json()
        assert project["patch"]["sampler_zones"] == saved.sampler_zones
        bundle = zipfile.ZipFile(io.BytesIO(client.get("/project.zip").content))
        assert {f"samples/{z.sample}.wav" for z in zones} <= set(bundle.namelist())
        # a format-2 preset keeps the map (each sample by its content id)
        client.post("/fragment/preset/save", data={"name": "keys"}, headers=HX)
        client.app.state.studio.patch = Patch()
        client.post("/fragment/preset/load", data={"name": "keys"}, headers=HX)
        assert client.app.state.studio.patch.sampler_zones == saved.sampler_zones
    with TestClient(create_app(data_dir=tmp_path)) as client:  # a restart
        assert client.app.state.studio.patch.sampler_zones == saved.sampler_zones
    # a hostile map in state is cleaned by the schema, never raised
    from synthlab.studio.schema import _patch

    junk = {
        **saved.to_json(),
        "sampler_zones": "../x,1,2;" + "0123456789abcdef,9,1,999",
    }
    assert _patch(junk)["sampler_zones"] == "0123456789abcdef,1,9,127"


def test_the_drawer_and_the_hub_offer_a_clip_as_a_zone(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    html = client.get("/view/editor").text
    assert html.count("/fragment/sampler/zone/add") >= 5  # a row each
    assert "/fragment/sampler/zones/map" in html  # a group's ⤷ zones / ⤷ keys
    perform = client.get("/view/perform").text
    assert "/fragment/sampler/zone/add" not in perform  # Synths' own tool
    hub = client.get("/view/clips", params={"item": "clip:a3"}).text
    assert "/fragment/sampler/zone/add" in hub


def test_the_zone_map_draws_each_box_where_its_zone_sits(tmp_path):
    _clips(tmp_path)
    client = TestClient(create_app(data_dir=tmp_path))
    _map(client, ["a2", "a3", "a4"])
    p = client.app.state.studio.patch
    ctx = sv.zone_context(p)
    z = ctx["zones"][1]
    assert (z["x"], z["w"]) == (round(52 / 1.28, 3), round(12 / 1.28, 3))
    assert z["span"] == "E3–D#4" and ctx["takes"] == 1
    html = client.get("/view/editor").text
    assert f'data-lo="{z["lo"]}" data-hi="{z["hi"]}"' in html
    assert 'role="slider"' in html and "/static/zonemap.js" in html
