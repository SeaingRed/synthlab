from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_bass_fragment_has_panels(tmp_path):
    r = make_client(tmp_path).get("/fragment/bass")
    assert r.status_code == 200
    for marker in ("bass-panels", "<svg", "mono", "phone"):
        assert marker in r.text


def test_phone_audition_fires_preview(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/bass/phone-audition")
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    import json

    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    wav = client.get(url)
    assert wav.content[:4] == b"RIFF"


def test_bass_meter_cards(tmp_path):
    html = make_client(tmp_path).get("/fragment/bass").text
    assert html.count("meter-card") >= 2
    assert "quickkeys-hint" in make_client(tmp_path).get("/view/bass").text


def test_bass_view_has_quick_keys(tmp_path):
    html = make_client(tmp_path).get("/view/bass").text
    for k in ("E1", "A1", "E2"):
        assert k in html


def test_quick_keys_post_to_bass_key(tmp_path):
    """The quick-key buttons post directly to /fragment/bass/key with only
    a root value — no hx-include, since the transport form isn't on this
    view's DOM (that was the bug: hx-include selectors matched nothing)."""
    html = make_client(tmp_path).get("/view/bass").text
    assert 'hx-post="/fragment/bass/key"' in html
    # the quick keys themselves: the clip library's rail button, on every
    # page since Oct 2026, includes its own two fields on purpose
    keys = [tag for tag in html.split("<") if 'hx-post="/fragment/bass/key"' in tag]
    assert keys and not any("hx-include" in tag for tag in keys)


def test_quick_key_preserves_transport_structure(tmp_path):
    # `with` runs the app lifespan: shutdown flushes the debounced save.
    with make_client(tmp_path) as client:
        # Set a distinctive bpm + a drum + a velocity via a normal transport post.
        client.post(
            "/fragment/arrangement",
            data={
                "bpm": "150",
                "bars": "2",
                "steps": "16",
                "scale_name": "pentatonic_minor",
                "drum.kick.0": "on",
                "vel.0": "0.5",
            },
        )

        # What the browser truly sends for the E2 quick key: root only.
        r = client.post("/fragment/bass/key", data={"root": "E2"})
        assert r.status_code == 200

    import json

    state = json.loads((tmp_path / "state.json").read_text())
    arr = state["arrangement"]
    assert arr["bpm"] == 150.0
    assert arr["bars"] == 2
    # the quick key is Bass Lab's own: it never touches the Perform grid
    # or the key (it used to set root=E2, cells=[[E2, 0]] and wipe the grid)
    assert arr["root"] == "A1"
    assert arr["cells"] == [["A1", 0], ["A1", 2]]  # the default grid, kept
    assert state["bass_root"] == "E2"
    assert arr["drums"] == [["kick", 0]]
    assert arr["velocities"]["0"] == 0.5


def test_quick_key_rejects_invalid_root(tmp_path):
    r = make_client(tmp_path).post("/fragment/bass/key", data={"root": "Z9"})
    assert r.status_code == 404
