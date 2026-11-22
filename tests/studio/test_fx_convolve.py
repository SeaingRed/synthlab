"""The convolution unit on the rack: a clip picked as its impulse becomes a
sample named by its content (`studio/irs.py`), the unit keeps the id, a
key carries it, a bundle carries it, the live keys load the same IR, and
hostile ids and oversized clips are cleaned or refused."""

from __future__ import annotations

import io

import numpy as np
from fastapi.testclient import TestClient
from scipy.io import wavfile
from scipy.signal import fftconvolve

from synthlab import effects as fx
from synthlab import sampler
from synthlab.core import SR
from synthlab.studio import irs, limits, projectzip
from synthlab.studio.agent.tools import AGENT_FX
from synthlab.studio.clips import load_clip, save_clip
from synthlab.studio.fxlive import FX_LIVE, live_chain
from synthlab.studio.fxrack import RACK_PRESSES, build_chain, clean_chain, edit_chain
from synthlab.studio.render import RenderInputs
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import preset

ED = {"HX-Current-URL": "http://testserver/view/editor", "Origin": "http://testserver"}
ARR = Arrangement(bpm=120.0)


def _room(seconds: float = 0.6, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    return (
        rng.standard_normal((n, 2)) * np.exp(-np.arange(n) / (0.15 * SR))[:, None] * 0.4
    )


def test_the_unit_and_its_bounds():
    assert limits.MAX_IR_SECONDS == fx.CONVOLVE_MAX_SECONDS
    assert FX_LIVE["convolve"] == "native" and FX_LIVE["utility"] == "js"
    # the sound agent offers neither (a clip it cannot pick; its schema's budget)
    assert "convolve" not in AGENT_FX and "utility" not in AGENT_FX
    assert "ir" in RACK_PRESSES
    (u,) = clean_chain([{"kind": "convolve"}])
    assert u["ir"] == "" and u["params"]["mix"] == 0.3


def test_a_hostile_impulse_id_is_cleaned_to_none():
    for bad in (
        "../../x",
        "ABCDEF0123456789",
        "0123456789abcde",
        "0" * 17,
        5,
        None,
        ["a"],
    ):
        (u,) = clean_chain([{"kind": "convolve", "ir": bad}])
        assert u["ir"] == "", bad
    chain = clean_chain([{"kind": "convolve"}])
    for bad in ("../x", "clip:nope", "0" * 17):
        try:
            edit_chain(chain, "set", {"index": "0", "name": "ir", "value": bad})
        except ValueError as err:
            assert "impulse" in str(err)
        else:  # pragma: no cover
            raise AssertionError(bad)
    # an id that is well formed but not here: kept (a bundle may bring it), a dry pass
    out, said, _ = edit_chain(
        chain, "set", {"index": "0", "name": "ir", "value": "0123456789abcdef"}
    )
    assert out[0]["ir"] == "0123456789abcdef" and said == "Convolution impulse"
    x = np.random.default_rng(0).standard_normal((SR // 4, 2))
    assert np.array_equal(fx.chain(x, build_chain(out, ARR)), x)


def test_the_key_carries_the_impulse_by_content():
    a, b = sampler.register(_room(seed=1)), sampler.register(_room(seed=2))
    p = preset("bass_reese")

    def key(ir):
        units = clean_chain([{"kind": "convolve", "params": {"mix": 0.5}, "ir": ir}])
        return RenderInputs(patch=p, arr=ARR, main_fx=units).key()

    assert len({key(""), key(a), key(b)}) == 3
    assert key(a) == key(a)


def test_a_picked_clip_becomes_the_units_impulse(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    save_clip(tmp_path, "hall", _room())
    save_clip(tmp_path, "too_long", _room(seconds=limits.MAX_IR_SECONDS + 1.0))
    client.post(
        "/fragment/fx/add", data={"scope": "layer", "kind": "convolve"}, headers=ED
    )
    page = client.get("/view/editor").text
    sel = page.split('aria-label="Convolution impulse"')[1].split("</select>")[0]
    assert 'value="clip:hall"' in sel and "too_long" not in sel
    assert '<option value="" selected>no impulse</option>' in sel
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "0", "name": "ir", "value": "clip:hall"},
        headers=ED,
    )
    assert r.status_code == 200
    unit = s.layers[s.active_layer]["fx"][0]
    want = sampler.sample_id(sampler.prepare(*load_clip(tmp_path, "hall")))
    assert unit["ir"] == want and (s.sample_dir / f"{want}.wav").is_file()
    assert 'value="clip:hall" selected' in r.text
    # over the cap: refused in words, the unit kept as it was
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "0", "name": "ir", "value": "clip:too_long"},
        headers=ED,
    )
    assert r.status_code == 422 and "at most" in r.text
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "0", "name": "ir", "value": "clip:nope"},
        headers=ED,
    )
    assert r.status_code == 422 and "gone" in r.text
    assert s.layers[s.active_layer]["fx"][0]["ir"] == want
    # one press, one undo step
    client.post("/fragment/undo", headers=ED)
    assert s.layers[s.active_layer]["fx"][0]["ir"] == ""


def test_a_clip_over_the_byte_cap_is_neither_offered_nor_taken(tmp_path, monkeypatch):
    save_clip(tmp_path, "big", _room())
    monkeypatch.setattr(limits, "MAX_IR_BYTES", 1000)
    assert [v for v, _l, _s in irs.choices(tmp_path, "")] == [""]
    try:
        irs.load(tmp_path, tmp_path / "samples", "big")
    except irs.IRError as err:
        assert "at most" in str(err)
    else:  # pragma: no cover
        raise AssertionError("taken")


def test_the_live_keys_load_the_loops_ir(tmp_path):
    """The served IR is `effects.convolve_ir` (float32 at the asked rate)
    and the unit is dry * (1 - mix) + that convolution — what a
    ConvolverNode with no normalisation plays. No IR here: no URL and mix
    0, the dry pass the loop is."""
    client = TestClient(create_app(data_dir=tmp_path))
    sid = sampler.save(client.app.state.studio.sample_dir, _room())
    units = [
        {
            "kind": "convolve",
            "params": {"mix": 0.4, "predelay": 0.01, "lowcut": 150.0},
            "ir": sid,
        }
    ]
    (live,) = live_chain(units, ARR, False)
    assert live["mix"] == 0.4 and live["ir"].startswith(
        f"/fx/ir.wav?kind=clip&id={sid}"
    )
    r = client.get(live["ir"] + "&sr=44100")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    rate, served = wavfile.read(io.BytesIO(r.content))
    assert rate == SR and served.dtype == np.float32
    room = fx.convolve_ir(sid, 0.01, 150.0, 20000.0)
    np.testing.assert_allclose(served, room.astype(np.float32))
    x = np.random.default_rng(1).standard_normal((SR // 2, 2)) * 0.1
    wet = np.column_stack(
        [
            fftconvolve(x[:, c], served[:, c].astype(np.float64))[: len(x)]
            for c in (0, 1)
        ]
    )
    eng = fx.chain(x, build_chain(units, ARR))
    assert np.max(np.abs(eng - (x * 0.6 + wet * 0.4))) < 1e-6
    assert client.get("/fx/ir.wav?kind=clip&id=0123456789abcdef").status_code == 404
    # a path is no id: never read (404), and past an id's length refused
    assert client.get("/fx/ir.wav?kind=clip&id=../../etc/passwd").status_code == 404
    assert client.get("/fx/ir.wav?kind=clip&id=../../../etc/passwd").status_code == 422
    (gone,) = live_chain([{**units[0], "ir": "0123456789abcdef"}], ARR, False)
    assert gone["ir"] is None and gone["mix"] == 0.0


def test_a_bundle_carries_the_impulse():
    sid = sampler.register(_room(seed=9))
    payload = {"layers": [{"fx": [{"kind": "convolve", "params": {}, "ir": sid}]}]}
    assert projectzip.sample_refs(payload) == {sid}
    assert projectzip.sample_refs({"x": [{"kind": "reverb", "ir": sid}]}) == set()
