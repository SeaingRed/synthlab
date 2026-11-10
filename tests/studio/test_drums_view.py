from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_view_drums_has_four_panels_and_pads(tmp_path):
    r = make_client(tmp_path).get("/view/drums")
    assert r.status_code == 200
    assert r.text.count('class="panel drum-panel module"') == 8  # the whole kit
    assert r.text.count('class="hit-pad"') == 8


def test_nav_contains_kit(tmp_path):
    """The drum page is "Kit" in the rail; its route stays /view/drums."""
    html = make_client(tmp_path).get("/").text
    assert ">Kit<" in html
    assert 'href="/view/drums"' in html


def test_post_fragment_drums_persists_value(tmp_path):
    # `with` runs the app lifespan: shutdown flushes the debounced save.
    with make_client(tmp_path) as client:
        r = client.post("/fragment/drums", data={"kick.tune": "40"})
        assert r.status_code == 200

    # re-read state directly
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    assert s.arrangement.drum_params["kick"]["tune"] == 40.0


def test_post_fragment_drums_clamps_out_of_range(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/fragment/drums", data={"kick.tune": "9999"})
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    assert s.arrangement.drum_params["kick"]["tune"] == 80.0  # clamped to max


def test_preset_post_applies_known_values(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/drums/preset", data={"voice": "kick", "preset": "808 Sub"}
    )
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    assert s.arrangement.drum_params["kick"]["tune"] == 36.0


def test_preset_post_unknown_voice_404(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/drums/preset", data={"voice": "nope", "preset": "x"})
    assert r.status_code == 404


def test_hit_wav_200_and_riff(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/drum/kick/hit.wav")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert r.headers.get("cache-control") == "no-store"


def test_hit_wav_unknown_voice_404(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/drum/nope/hit.wav")
    assert r.status_code == 404


def _hit_url(html: str, voice: str) -> str:
    marker = f'id="hitpad-{voice}"'
    tail = html[html.index(marker) :]
    attr = 'data-hit-url="'
    start = tail.index(attr) + len(attr)
    return tail[start : tail.index('"', start)]


def test_data_hit_url_hash_changes_with_params(tmp_path):
    client = make_client(tmp_path)
    url1 = _hit_url(client.get("/view/drums").text, "kick")
    assert "k=" in url1
    client.post("/fragment/drums", data={"kick.tune": "40"})
    url2 = _hit_url(client.get("/view/drums").text, "kick")
    k1 = url1.split("k=")[1]
    k2 = url2.split("k=")[1]
    assert k1 != k2
