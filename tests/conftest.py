"""Suite-wide guards.

The sound agent reads its Gemini key from the checkout's `.env` and the
environment. No test may see a real key or reach the network: every test
runs with the key variables removed, `.env` pointed at a file that does not
exist, and the real Gemini client replaced by one that refuses to be built
(tests that need a model hand `AgentService` a scripted fake).

Starlette's TestClient sends `Host: testserver`. The studio's boundary
allows loopback names only (`security.LOCAL_HOSTS`), so the suite adds
that one name for itself here rather than the shipped allowlist carrying
it; `production_hosts` is the list as shipped.
"""

from __future__ import annotations

import pytest

from synthlab.studio import security

PRODUCTION_HOSTS = security.LOCAL_HOSTS  # read before any test patches it


class _NoNetworkClient:
    def __init__(self, *a, **kw):
        raise AssertionError("a test tried to build the real Gemini client")


@pytest.fixture(autouse=True)
def _no_agent_secrets(monkeypatch, tmp_path_factory):
    from synthlab.studio import takecoach
    from synthlab.studio.agent import config, service

    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        config, "ENV_FILE", tmp_path_factory.getbasetemp() / "no-such.env"
    )
    monkeypatch.setattr(service, "GeminiClient", _NoNetworkClient)
    # a take's clean-up builds its own SDK client (takecoach.client_for)
    monkeypatch.setattr(takecoach, "client_for", _NoNetworkClient)


@pytest.fixture(autouse=True)
def _no_model_download(monkeypatch, tmp_path_factory):
    """No test fetches the separation model or finds one in the user's
    cache: the model dir is an empty temp folder and the downloader's
    opener refuses (tests that exercise a download hand it their own)."""
    from synthlab import stems

    def refuse(url):
        raise OSError("a test tried to download the separation model")

    monkeypatch.setenv(stems.MODEL_ENV, str(tmp_path_factory.getbasetemp() / "models"))
    monkeypatch.setattr(stems, "_open", refuse)


@pytest.fixture(autouse=True)
def _no_vendor_download(monkeypatch):
    """No test fetches GSAP from npm (`vendor.ensure` runs on a real launch):
    the downloader refuses, so a launch in a test runs without it."""
    from synthlab.studio import vendor

    def refuse(url, timeout=30.0):
        raise OSError("a test tried to download from the npm registry")

    monkeypatch.setattr(vendor, "_download", refuse)


@pytest.fixture(autouse=True, scope="session")
def _testclient_host():
    """Every `create_app()` a test builds accepts the TestClient's Host.
    Session-scoped: a module-scoped client is built before any per-test
    fixture runs."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(security, "LOCAL_HOSTS", PRODUCTION_HOSTS | {"testserver"})
        yield


@pytest.fixture
def production_hosts():
    """The Host allowlist a real launch gets: `create_app(allowed_hosts=...)`."""
    return PRODUCTION_HOSTS
