"""Your own wavetables through the studio (routes/wavetable.py): made
from a clip or drawn, picked in the picker,
served to the live voice, and what a hostile post gets."""

from __future__ import annotations

import math

import numpy as np
from fastapi.testclient import TestClient

from synthlab import wavetables
from synthlab.core import SR
from synthlab.studio import limits
from synthlab.studio.clips import save_clip
from synthlab.studio.forms import patch_from_form
from synthlab.studio.server import create_app
from synthlab.synth import Patch

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _drawn(*ks) -> str:
    return ";".join(
        ",".join(f"{math.sin(2 * math.pi * k * i / 64):.3f}" for i in range(64))
        for k in ks
    )


def _client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def test_a_drawn_table_is_stored_picked_and_served(tmp_path):
    client = _client(tmp_path)
    st = client.app.state.studio
    r = client.post(
        "/fragment/wavetable/draw",
        data={"points": _drawn(1, 5), "name": "Two"},
        headers=HX,
    )
    assert r.status_code == 200 and 'id="view"' in r.text
    tid = st.patch.wt_custom
    assert wavetables.valid_id(tid) and st.patch.osc1 == "wavetable"
    assert (tmp_path / "wavetables" / f"{tid}.npy").is_file()
    assert f'value="own:{tid}"' in r.text  # the picker's own radio, checked
    assert "studio-toast" in r.headers["HX-Trigger"]
    # the live voice's payload, and /patch.json naming it
    live = client.get(f"/wavetable/live/{tid}.bin")
    assert live.status_code == 200
    assert "immutable" in live.headers["cache-control"]
    assert client.get("/patch.json").json()["wt_live"] == f"/wavetable/live/{tid}.bin"
    assert client.get("/wavetable/live/0123456789abcdef.bin").status_code == 404
    assert client.get("/wavetable/live/..%2Fstate.bin").status_code == 404
    # one undo step takes it back
    client.post("/fragment/undo", headers=HX)
    assert st.patch.wt_custom == ""


def test_a_clip_or_a_sample_becomes_a_table(tmp_path):
    client = _client(tmp_path)
    st = client.app.state.studio
    t = np.arange(SR) / SR
    save_clip(tmp_path, "hum", 0.4 * np.sin(2 * np.pi * 110 * t))
    menu = client.get("/fragment/wavetable/sources", headers=HX).text
    assert 'value="clip:hum"' in menu
    r = client.post(
        "/fragment/wavetable/clip",
        data={"source": "clip:hum", "frames": "999", "mode": "pitch"},
        headers=HX,
    )
    assert r.status_code == 200
    info = wavetables.info(st.patch.wt_custom)
    assert info["frames"] == limits.WT_FRAMES_RANGE[1] and info["name"] == "hum"
    # nothing to read, or silence: a toast, the patch untouched
    before = st.patch
    for data in ({"source": "clip:gone"}, {"source": "stem:x"}, {}):
        assert (
            client.post("/fragment/wavetable/clip", data=data, headers=HX).status_code
            == 422
        )
    save_clip(tmp_path, "quiet", np.zeros(SR))
    r = client.post(
        "/fragment/wavetable/clip", data={"source": "clip:quiet"}, headers=HX
    )
    assert r.status_code == 422 and st.patch == before


def test_a_hostile_drawing_is_a_toast_and_a_big_body_a_413(tmp_path):
    client = _client(tmp_path)
    for points in (
        "",
        "1,2,3",
        _drawn(1, 2, 3, 4, 5),
        "nan," * 63 + "0",
        "0," * 63 + "0",
    ):
        r = client.post("/fragment/wavetable/draw", data={"points": points}, headers=HX)
        assert r.status_code == 422, points[:20]
    big = "0," * (limits.MAX_WT_DRAW_BYTES // 2 + 10)
    r = client.post("/fragment/wavetable/draw", data={"points": big}, headers=HX)
    assert r.status_code == 413


def test_the_picker_posts_own_and_built_in_tables():
    spec = wavetables.spectra(np.sign(np.sin(2 * np.pi * np.arange(2048) / 2048))[None])
    tid = wavetables.register(spec)
    base = Patch(osc1="wavetable", wt_table="vocal")
    own = patch_from_form({"wt_table": f"own:{tid}"}, base)
    assert (own.wt_custom, own.wt_table) == (tid, "vocal")
    # a built-in posted while one plays: back to the built-ins, even the same one
    back = patch_from_form({"wt_table": "vocal"}, own)
    assert (back.wt_custom, back.wt_table) == ("", "vocal")
    # a forged own value is no table
    assert patch_from_form({"wt_table": "own:../x"}, base) == base
    assert patch_from_form({"wt_table": f"own:{tid}"}, own) == own


def test_wt_custom_is_cleaned_on_every_load():
    from synthlab.params import clamp

    assert clamp(Patch(wt_custom="../../etc")).wt_custom == ""
    assert clamp(Patch(wt_custom="0123456789abcdef")).wt_custom == "0123456789abcdef"
