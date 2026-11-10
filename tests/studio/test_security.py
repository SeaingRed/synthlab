import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits
from synthlab.studio.server import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def test_foreign_host_rejected(client):
    r = client.get("/", headers={"host": "evil.example:8420"})
    assert r.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8420", "localhost:8420"])
def test_local_hosts_allowed(client, host):
    assert client.get("/health", headers={"host": host}).status_code == 200


def test_cross_site_post_rejected(client):
    r = client.post("/fragment/drums/mutate", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_same_site_post_rejected(client):
    r = client.post("/fragment/drums/mutate", headers={"sec-fetch-site": "same-site"})
    assert r.status_code == 403


def test_origin_mismatch_rejected(client):
    r = client.post(
        "/fragment/drums/mutate",
        headers={"host": "127.0.0.1:8420", "origin": "http://evil.example"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8420", "localhost:8420"])
def test_same_origin_post_allowed(client, host):
    r = client.post(
        "/fragment/drums/mutate",
        headers={
            "host": host,
            "origin": f"http://{host}",
            "sec-fetch-site": "same-origin",
        },
    )
    assert r.status_code == 200


def test_non_browser_post_allowed(client):  # no Origin, no Sec-Fetch-*: curl/tests
    assert client.post("/fragment/drums/mutate").status_code == 200


def test_frame_headers(client):
    r = client.get("/")
    # no other origin frames the studio; its own Song page docks
    # the Synths page in a frame
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


# The whole policy, pinned: loosening any directive is a deliberate edit here.
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; "
    "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)


def test_csp_is_pinned_and_grants_wasm_only(client):
    """script-src gains exactly 'wasm-unsafe-eval' — WebAssembly compilation
    for the live voice's DSP module (static/voice-dsp.wasm) — and never
    'unsafe-eval', which would also open eval()/new Function() (and so
    htmx's `js:` attribute values, see below)."""
    csp = client.get("/").headers["content-security-policy"]
    assert csp == CSP
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    script = directives["script-src"].split()
    assert script == ["'self'", "'wasm-unsafe-eval'"]
    tokens = csp.replace(";", " ").split()
    assert "'unsafe-eval'" not in tokens
    assert tokens.count("'wasm-unsafe-eval'") == 1
    # the policy covers the worklet modules and the wasm file too
    for path in ("/static/voice-worklet.js", "/static/voice-dsp.wasm"):
        assert client.get(path).headers["content-security-policy"] == CSP


def test_no_template_uses_js_prefixed_attribute_values():
    """htmx4 runs a `js:`-prefixed attribute value (e.g. hx-vals="js:{...}")
    through Function()/AsyncFunction, which this app's CSP blocks
    (script-src 'self', no 'unsafe-eval' — see security.py's CSP). curve.html
    used to read the click offset this way; it now gets x/y/w/h from
    audio.js's htmx:config:request listener instead. Pins that no template
    regresses back to a js:/javascript: attribute value."""
    pattern = re.compile(r"""=["'](?:js:|javascript:)""", re.IGNORECASE)
    offenders = [
        str(path)
        for path in Path("synthlab/studio/templates").rglob("*.html")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"js:/javascript: attribute values found in: {offenders}"


def test_debug_stats_gated(client, monkeypatch):
    assert client.get("/debug/stats").status_code == 404
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    assert client.get("/debug/stats").status_code == 200


def test_import_error_is_generic(client):
    r = client.post(
        "/project/import", files={"file": ("p.json", b"{bad", "application/json")}
    )
    assert r.status_code == 422
    assert "Expecting" not in r.text  # no json.decoder exception text echoed
    assert "That isn" in r.text and "a valid synthlab project file." in r.text


# share_token: minimal coverage here (the CLI flag and the rest of the
# behaviour are tested elsewhere) — just proving the branch in security.py
# actually gates and unlocks access.


def test_share_token_required_without_token(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path, share_token="s3cret"))
    assert c.get("/").status_code == 401


def test_share_token_in_query_unlocks_and_sets_cookie(tmp_path):
    c = TestClient(
        create_app(data_dir=tmp_path, share_token="s3cret"), follow_redirects=False
    )
    r = c.get("/?token=s3cret")
    assert r.status_code == 303
    # named per port (TestClient's host has none: 80)
    assert c.cookies.get("synthlab_token_80") == "s3cret"
    assert c.get("/").status_code == 200


def test_share_token_required(tmp_path):
    """Full brief scenario: with share_token set, the Host check is skipped
    (LAN IPs vary) but the token gate still applies."""
    c = TestClient(
        create_app(data_dir=tmp_path, share_token="s3cret"), follow_redirects=False
    )
    assert c.get("/", headers={"host": "192.168.1.5:8420"}).status_code == 401
    r = c.get("/?token=s3cret", headers={"host": "192.168.1.5:8420"})
    assert r.status_code == 303 and "synthlab_token_8420=" in r.headers["set-cookie"]
    c.cookies.set("synthlab_token_8420", "s3cret")
    assert c.get("/", headers={"host": "192.168.1.5:8420"}).status_code == 200


def test_the_share_cookie_is_named_per_port(tmp_path):
    """Cookies ignore the port: two shared studios on one machine wrote one
    `synthlab_token` and each refused the other's link."""
    c = TestClient(
        create_app(data_dir=tmp_path, share_token="s3cret"), follow_redirects=False
    )
    for port in (8420, 8421):
        r = c.get("/?token=s3cret", headers={"host": f"192.168.1.5:{port}"})
        assert f"synthlab_token_{port}=s3cret" in r.headers["set-cookie"]
    c.cookies.clear()
    c.cookies.set("synthlab_token_8421", "s3cret")
    assert c.get("/", headers={"host": "192.168.1.5:8420"}).status_code == 401
    assert c.get("/", headers={"host": "192.168.1.5:8421"}).status_code == 200


def test_wrong_token_refused(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path, share_token="s3cret"))
    c.cookies.set("synthlab_token_80", "nope")
    assert c.get("/").status_code == 401


# -- the token check, hardened -----------------------------------------------


def test_non_ascii_query_token_rejected_not_500(tmp_path):
    """hmac.compare_digest raises TypeError on a non-ASCII str, which used
    to surface as a 500 instead of a 401 for e.g. ?token=é."""
    c = TestClient(create_app(data_dir=tmp_path, share_token="s3cret"))
    assert c.get("/?token=é").status_code == 401


def test_non_ascii_cookie_token_rejected_not_500(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path, share_token="s3cret"))
    # httpx's own cookie jar refuses to send a non-ASCII cookie *value* via
    # .cookies.set() (raises before the request leaves the client), so the
    # header is built by hand with a raw utf-8 byte value — the same thing
    # a non-conforming client/proxy can put on the wire.
    r = c.get("/", headers=[("cookie", "synthlab_token_80=é".encode())])
    assert r.status_code == 401


def test_share_cookie_is_samesite_lax(tmp_path):
    """strict withheld the cookie on the first cross-site navigation a share
    link is opened from (a chat app, say), so the recipient saw a 401."""
    c = TestClient(
        create_app(data_dir=tmp_path, share_token="s3cret"), follow_redirects=False
    )
    r = c.get("/?token=s3cret")
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_null_origin_rejected(client):
    """Origin: null used to be exempt from the Origin check; no studio flow
    legitimately sends it."""
    r = client.post(
        "/fragment/drums/mutate",
        headers={"host": "127.0.0.1:8420", "origin": "null"},
    )
    assert r.status_code == 403


def test_ipv6_loopback_host_allowed(client):
    assert client.get("/health", headers={"host": "[::1]:8420"}).status_code == 200


# -- request body size is bounded at the ASGI boundary, before
# routing or Starlette's own multipart parser (which spools file parts to
# disk with no size cap of its own) ever sees the body. ----------------------


def test_declared_oversize_body_rejected_before_the_handler_runs(tmp_path, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_UPLOAD_BYTES", 1000)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        # A real, larger-than-the-cap multipart body: httpx computes an
        # honest Content-Length for it, so this exercises the *declared*
        # size leg (rejected before `receive()` is ever called), not the
        # chunked/running-total leg.
        big = b"\0" * 5000
        r = client.post("/clip/upload", files={"file": ("j.wav", big, "audio/wav")})
        assert r.status_code == 413
        # the handler never ran: nothing was written to the clip store
        assert not (tmp_path / "clips" / "jam.wav").exists()


def test_undeclared_chunked_body_over_cap_gets_413(monkeypatch):
    """No TestClient/httpx path reliably sends a body with no Content-Length
    (it always computes one for bytes/file content), so the running-total
    leg of the ASGI wrapper is exercised directly against a fake `receive`
    that streams chunks the way a real chunked-transfer-encoding request
    would, with no content-length header in scope."""
    from synthlab.studio.security import BodySizeLimit

    monkeypatch.setattr("synthlab.studio.limits.MAX_UPLOAD_BYTES", 1000)

    async def inner_app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

    middleware = BodySizeLimit(inner_app)

    async def run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/clip/upload",
            "headers": [],  # no content-length: undeclared/chunked
        }
        chunks = [b"x" * 800, b"x" * 800]  # 1600 total, over the 1000 cap
        sent = []

        async def fake_receive():
            if chunks:
                chunk = chunks.pop(0)
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": bool(chunks),
                }
            return {"type": "http.request", "body": b"", "more_body": False}

        async def fake_send(message):
            sent.append(message)

        await middleware(scope, fake_receive, fake_send)
        return sent

    sent = asyncio.run(run())
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status == 413


def test_small_normal_post_is_unaffected_by_the_body_guard(client):
    assert client.post("/fragment/drums/mutate").status_code == 200


def test_voice_take_path_uses_its_own_tighter_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_TAKE_UPLOAD_BYTES", 1000)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        big = b"\0" * 5000
        r = client.post("/voice/take", files={"file": ("j.wav", big, "audio/wav")})
        assert r.status_code == 413


def test_project_import_path_uses_its_own_tighter_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_PROJECT_UPLOAD_BYTES", 1000)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        big = b"{" + b'"x": "' + b"0" * 5000 + b'"}'
        r = client.post(
            "/project/import", files={"file": ("p.json", big, "application/json")}
        )
        assert r.status_code == 413


def test_project_import_path_is_capped_at_the_bundle_size(tmp_path, monkeypatch):
    """/project/import also takes a bundle (/project.zip), so the body cap
    on the path is MAX_PROJECT_ZIP_BYTES; the JSON cap above is the
    route's own, for a body that turns out not to be a zip."""
    from synthlab.studio.security import BodySizeLimit

    assert BodySizeLimit._limit_for("/project/import") == limits.MAX_PROJECT_ZIP_BYTES
    assert limits.MAX_PROJECT_ZIP_BYTES > limits.MAX_PROJECT_UPLOAD_BYTES
    monkeypatch.setattr("synthlab.studio.limits.MAX_PROJECT_ZIP_BYTES", 1000)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        big = b"PK\x03\x04" + b"\0" * 5000
        r = client.post(
            "/project/import", files={"file": ("p.zip", big, "application/zip")}
        )
        assert r.status_code == 413


# -- review round: reads from another site, the allowlist, the bind ----------

NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}


def test_testserver_is_not_a_production_host(tmp_path, production_hosts):
    """TestClient's own hostname sat in the shipped allowlist; the suite
    adds it for itself now (tests/conftest.py)."""
    assert "testserver" not in production_hosts
    app = create_app(data_dir=tmp_path, allowed_hosts=production_hosts)
    assert TestClient(app).get("/health").status_code == 400
    c = TestClient(app, base_url="http://127.0.0.1:8420")
    assert c.get("/health").status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
@pytest.mark.parametrize("path", ["/view/breed", "/fx/ir.wav", "/export/stems.zip"])
def test_a_read_another_site_embeds_is_refused(client, site, path):
    """GETs were never checked, and these three render or change state:
    an <img> or fetch() on any web page reached them."""
    headers = {"sec-fetch-site": site, "sec-fetch-mode": "no-cors"}
    r = client.get(path, headers={**headers, "sec-fetch-dest": "image"})
    assert r.status_code == 403
    assert client.head(path, headers=headers).status_code == 403
    s = client.app.state.studio
    assert s.breed_tiles == [] and s.breed_history == []


def test_a_link_from_another_site_lands_on_the_root_only(client):
    """A top-level navigation is how a share link is followed, so it is
    served — at `/`. Anywhere else it is sent there: a link to /view/breed
    must not breed a litter (and spend the starred parents) either."""
    cross = {"sec-fetch-site": "cross-site", **NAVIGATE}
    assert client.get("/", headers=cross).status_code == 200
    s = client.app.state.studio
    s.breed_starred = ["x"]
    r = client.get("/view/breed", headers=cross, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert s.breed_tiles == [] and s.breed_starred == ["x"]
    r = client.get("/export/stems.zip", headers=cross, follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.parametrize(
    "headers",
    [
        {},  # curl, tests, old browsers
        {"sec-fetch-site": "none", **NAVIGATE},  # the address bar, a bookmark
        {"sec-fetch-site": "same-origin", "sec-fetch-mode": "cors"},  # htmx
        {"sec-fetch-site": "same-origin", "sec-fetch-dest": "audio"},
    ],
)
def test_the_studios_own_reads_are_untouched(client, headers):
    assert client.get("/view/perform", headers=headers).status_code == 200
    assert client.get("/project.json", headers=headers).status_code == 200


def test_a_share_link_opened_from_another_app_still_works(tmp_path):
    c = TestClient(
        create_app(data_dir=tmp_path, share_token="s3cret"), follow_redirects=False
    )
    lan = {"host": "192.168.1.5:8420", "sec-fetch-site": "cross-site", **NAVIGATE}
    r = c.get("/?token=s3cret", headers=lan)
    assert r.status_code == 303 and r.headers["location"].endswith("/")
    c.cookies.set("synthlab_token_8420", "s3cret")
    # the redirect is still the cross-site navigation that started it
    assert c.get("/", headers=lan).status_code == 200
    # the token admits the device, not another site's embeds on it
    embed = {**lan, "sec-fetch-mode": "no-cors", "sec-fetch-dest": "image"}
    assert c.get("/export/stems.zip", headers=embed).status_code == 403
