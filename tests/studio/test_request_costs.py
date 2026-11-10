"""Work a request used to do for nothing, pinned so it stays gone."""

from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio import service
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_swapping_to_the_song_page_renders_no_loop(tmp_path, monkeypatch):
    """The Song page plays /song/plan.json, never a loop preview, so the
    htmx swap to it renders none (and cues none)."""
    client = make_client(tmp_path)
    renders: list = []
    real = service.render_loop
    monkeypatch.setattr(
        service, "render_loop", lambda *a, **k: renders.append(1) or real(*a, **k)
    )
    r = client.get("/view/song", headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'id="song-topbar"' in r.text
    assert "HX-Trigger" not in r.headers and renders == []
    # every other view still cues its own mix
    r = client.get("/view/perform", headers={"HX-Request": "true"})
    assert "preview-ready" in r.headers["HX-Trigger"]


def test_revved_static_urls_are_immutable(tmp_path):
    """Each file carries its own hash (`asset`), so an edit to one file
    re-downloads that file alone; a URL with any other `v` revalidates."""
    client = make_client(tmp_path)
    hashes = client.app.state.svc.templates.env.globals["static_hashes"]
    rev = hashes["audio.js"]
    page = client.get("/").text
    assert f"/static/audio.js?v={rev}" in page
    assert f"/static/motion.js?v={hashes['motion.js']}" in page
    assert rev != hashes["motion.js"]  # not one rev for the folder
    cached = client.get(f"/static/audio.js?v={rev}")
    assert cached.status_code == 200
    assert "immutable" in cached.headers["cache-control"]
    # unrevved, stale or another file's rev keep plain revalidation
    for url in (
        "/static/audio.js",
        "/static/audio.js?v=stale",
        f"/static/audio.js?v={hashes['motion.js']}",
    ):
        r = client.get(url)
        assert r.status_code == 200 and "etag" in r.headers
        assert "immutable" not in r.headers.get("cache-control", "")
    assert client.get(f"/static/nope.js?v={rev}").status_code == 404


def test_a_slider_post_builds_no_page_context(tmp_path, monkeypatch):
    """A patch post's regions (the reset menu, "edited", the chain bar, the
    drawings) render from their lean contexts: a whole `page_context` (both
    envelope drawings, the filter curve, the XY and macro contexts) per
    slider post was built only to be thrown away."""
    client = make_client(tmp_path)
    svc = client.app.state.svc
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    client.post("/fragment/patch", data={"cutoff": "800"}, headers=hx)  # warm
    built: list = []
    real = svc.page_context
    monkeypatch.setattr(
        svc, "page_context", lambda *a, **k: built.append(1) or real(*a, **k)
    )
    r = client.post("/fragment/patch", data={"cutoff": "900"}, headers=hx)
    assert 'id="signal-chain"' in r.text and 'id="filter-response-wrap"' in r.text
    assert built == []
