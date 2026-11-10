import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio import worklet
from synthlab.studio.factory import FACTORY
from synthlab.studio.server import create_app
from synthlab.studio.soundmeta import note_duration, preset_meta
from synthlab.synth import Patch, Synth


def make_client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def test_factory_patches_are_audible_and_meta_fresh():
    # a demo per engine feature: the oscillators', and filters and
    # modulation's among them
    assert len(FACTORY) == 31
    for entry in FACTORY:
        p = Patch(**entry["patch"])
        audio = Synth(p).note("C3", note_duration(p))
        assert float(np.max(np.abs(audio))) > 0.05, entry["name"]
        fresh = preset_meta(p)
        assert (
            max(
                abs(a - b)
                for a, b in zip(entry["spectrum"], fresh["spectrum"], strict=True)
            )
            < 0.15
        ), entry["name"]


def test_factory_routes(tmp_path):
    client = make_client(tmp_path)
    name = FACTORY[0]["name"]
    w = client.get(f"/factory/{name}/audition.wav")
    assert w.status_code == 200 and w.content[:4] == b"RIFF"
    r = client.post("/fragment/factory/load", data={"name": name})
    assert r.status_code == 200
    assert client.get("/factory/nope/audition.wav").status_code == 404
    bad = client.post("/fragment/factory/load", data={"name": "nope"})
    assert bad.status_code == 422
    assert "Factory sound not found." in bad.text


def test_factory_cards_render(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/patch", data={"cutoff": "555"})
    client.post("/fragment/preset/save", data={"name": "deep one"})
    html = client.get("/").text
    assert "Interstellar Whoop" in html
    assert html.count("data-audition-url") >= 9  # 8 factory + 1 user preset
    assert "Factory sounds arrive" not in html  # empty-state gone


def test_glide_forward_factory_patches_are_worklet_playable():
    by_name = {e["name"]: Patch(**e["patch"]) for e in FACTORY}
    assert worklet.can_play(by_name["Interstellar Whoop"])
    for name, p in by_name.items():
        if p.glide > 0:
            assert worklet.can_play(p), (
                f"{name} promises glide but is WAV-gated: {worklet.blockers(p)}"
            )
