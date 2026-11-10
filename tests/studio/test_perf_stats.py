"""Instrumentation for the "why is it slow" question: request timing,
render timing, and cache hit rates, surfaced at /debug/stats and as a
Server-Timing header the browser devtools can graph per request.

Born from a session where slider drags felt laggy and previews arrived
late with nothing measurable anywhere: no logging, no timings, and cache
hit rates that could only be guessed at.
"""

import logging
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.render import RenderCache, RenderResult, StemCache
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_debug_stats_reports_requests_renders_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    client = make_client(tmp_path)
    # identical posts: first is a render-cache miss, second a hit
    client.post("/fragment/patch", data={"cutoff": "500"})
    client.post("/fragment/patch", data={"cutoff": "500"})
    r = client.get("/debug/stats")
    assert r.status_code == 200
    stats = r.json()
    req = stats["requests"]["POST /fragment/patch"]
    assert req["count"] >= 2
    assert req["avg_ms"] > 0
    assert req["max_ms"] >= req["avg_ms"]
    assert stats["renders"]["count"] >= 1
    assert stats["renders"]["last_ms"] > 0
    assert stats["render_cache"]["misses"] >= 1
    assert stats["render_cache"]["hits"] >= 1
    assert "stem_cache" in stats


def test_fragment_responses_carry_server_timing(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/patch", data={"cutoff": "500"})
    assert "app;dur=" in r.headers.get("server-timing", "")


def test_static_requests_are_not_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    client = make_client(tmp_path)
    client.get("/static/htmx.min.js")
    stats = client.get("/debug/stats").json()
    assert not any("/static" in k for k in stats["requests"])


def test_stem_cache_counts_hits_misses_evictions():
    c = StemCache(max_entries=2)
    assert c.get("a") is None  # miss
    c.put("a", np.zeros(4))
    assert c.get("a") is not None  # hit
    c.put("b", np.zeros(4))
    c.put("c", np.zeros(4))  # evicts "a"
    s = c.stats()
    # byte-bounded like RenderCache, so it reports its bytes too
    assert s == {"hits": 1, "misses": 1, "evictions": 1, "entries": 2, "bytes": 64}


def test_render_cache_counts_hits_and_misses():
    c = RenderCache(max_entries=2)
    assert c.get("k") is None
    c.put(RenderResult(key="k", wav=b"", analysis={}))
    assert c.get("k") is not None
    assert c.stats() == {
        "hits": 1,
        "misses": 1,
        "evictions": 0,
        "entries": 1,
        "bytes": 0,
    }


def test_requests_log_under_the_studio_logger(tmp_path, caplog):
    client = make_client(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="synthlab.studio"):
        client.post("/fragment/patch", data={"cutoff": "510"})
    lines = [r.getMessage() for r in caplog.records]
    assert any("/fragment/patch" in line and "ms" in line for line in lines)


def test_preview_trigger_renders_are_timed(tmp_path, monkeypatch):
    """`preview_trigger` (the reset/bank/perform-launch preview path) must
    go through the same timed-and-cached `svc.render` as `do_render` —
    it used to render and cache without ever calling `perf.record_render`,
    so a whole class of previews was invisible in /debug/stats."""
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    client = make_client(tmp_path)
    before = client.get("/debug/stats").json()["renders"]["count"]
    client.post(
        "/fragment/reset",
        data={"scope": "all", "mode": "defaults"},
        headers={"HX-Current-URL": "http://testserver/view/perform"},
    )
    assert client.get("/debug/stats").json()["renders"]["count"] > before


def test_no_inline_toast_renders():
    """Every 422 toast used to build its own `fragments/toast.html` render
    inline (33 near-identical copies) — the template name should appear
    exactly once in the studio's Python now, inside the `toast()` helper
    (`StudioService.toast`, since server.py was split into routers)."""
    import synthlab.studio as studio_pkg

    root = Path(studio_pkg.__file__).parent
    hits = {
        str(p.relative_to(root)): n
        for p in root.rglob("*.py")
        if (n := p.read_text(encoding="utf-8").count("fragments/toast.html"))
    }
    assert hits == {str(Path("service") / "respond.py"): 1}


def test_requests_are_counted_by_route_not_by_url(tmp_path, monkeypatch):
    """The table was keyed on the raw path, 404s included, and never
    evicted: every preview key, clip name and probe added a row for good.
    It is keyed on the matched route's template now, so it can only grow
    to the size of the route list; what no route answers is one row."""
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    client = make_client(tmp_path)
    for i in range(20):
        assert client.get(f"/preview/nope{i}.wav").status_code == 404
        assert client.get(f"/clip/missing {i}.wav").status_code == 404
        assert client.get(f"/no/such/route/{i}").status_code == 404
    client.get("/view/perform")
    client.get("/view/drums")
    requests = client.get("/debug/stats").json()["requests"]
    assert requests["GET /clip/{name}.wav"]["count"] == 20
    assert requests["GET (unmatched)"]["count"] == 20
    assert requests["GET /view/{name}"]["count"] == 2
    preview = [k for k in requests if k.startswith("GET /preview/")]
    assert len(preview) == 1 and requests[preview[0]]["count"] == 20
    assert not any("nope" in k or "missing" in k or "such" in k for k in requests)
    assert len(client.app.state.svc.perf.requests) <= 5
