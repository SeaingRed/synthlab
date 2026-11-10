"""The Sound agent's prefix cache (`agent/client.py`), network-free.

`GeminiClient` is driven here with a scripted stand-in for the SDK client
(`FakeSDK`: `caches.create` and `models.generate_content` that record what
they were handed and answer or raise from a script), so the cache logic
runs against the SDK's real request *types* and nothing else. One test goes
a layer lower — the real SDK over an `httpx.MockTransport` — to pin the
bytes that would go on the wire. None of it sees a key or a socket.

Pinned here:
- the cache is created once per (key, model, prefix) and reused, across
  calls and across clients of the process;
- a cached request carries `cached_content` and none of the static prefix;
- an expired cache, or one the service no longer has, is made again;
- ANY error from a cache call falls back to the uncached request and the
  call (and the turn) succeeds; a prefix the API refuses (400 / 403 / 404)
  is not retried per call, while a transient error (429, 408, 5xx, the
  network) costs that one call its cache and nothing after it; either is
  logged once, without the key;
- `config.cache` off (the default) builds exactly the uncached request;
- `tools/agent_eval.py --dry-run` builds every request offline.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC

import pytest

from synthlab.studio.agent import client as aclient
from synthlab.studio.agent import context as actx
from synthlab.studio.agent import tools as atools
from synthlab.studio.agent.client import (
    CACHES,
    AgentError,
    GeminiClient,
    UserTurn,
    estimate_tokens,
    prefix_blob,
)
from synthlab.studio.agent.config import AgentConfig, load_config
from tests.studio.test_agent import SECRET, agent_client, turn

types = pytest.importorskip("google.genai.types")
errors = pytest.importorskip("google.genai.errors")

SYSTEM = actx.SYSTEM.format(rounds=3)
TOOLS = atools.declarations()
STATIC = ("system_instruction", "tools", "tool_config")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """The registry is the process's: every test starts with none."""
    CACHES.clear()
    clock = Clock()
    monkeypatch.setattr(aclient, "monotonic", clock)
    yield clock
    CACHES.clear()


def api_error(code: int, message: str) -> Exception:
    return errors.ClientError(code, {"error": {"code": code, "message": message}})


def response(cached: int = 0):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text="ok")]
                )
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=3000,
            cached_content_token_count=cached or None,
            candidates_token_count=10,
            total_token_count=3010,
        ),
        model_version="gemini-2.5-flash",
    )


class FakeSDK:
    """`genai.Client`'s two modules, scripted. `creates` / `cached` /
    `plain` are popped per call of that sort (an exception is raised);
    empty, they succeed."""

    def __init__(self, creates=(), cached=(), plain=(), on_cached=None):
        self.script = {"create": list(creates), "cached": list(cached), "plain": []}
        self.script["plain"] = list(plain)
        self.created: list = []  # the CreateCachedContentConfig of each create
        self.sent: list = []  # the GenerateContentConfig of each generate
        self.on_cached = on_cached
        self.caches = self
        self.models = self

    def _next(self, kind: str):
        item = self.script[kind].pop(0) if self.script[kind] else None
        if isinstance(item, BaseException):
            raise item

    def create(self, *, model, config):
        self.created.append(config)
        self._next("create")
        return types.CachedContent(name=f"cachedContents/c{len(self.created)}")

    def generate_content(self, *, model, contents, config):
        self.sent.append(config)
        if config.cached_content:
            if self.on_cached:
                self.on_cached()
            self._next("cached")
            return response(cached=2900)
        self._next("plain")
        return response()


def gemini(sdk: FakeSDK, **cfg) -> GeminiClient:
    client = GeminiClient.__new__(GeminiClient)  # no SDK client, no network
    client._types = types
    client._config = AgentConfig(api_key=SECRET, **{"cache": True, **cfg})
    client._client = sdk
    return client


def ask(client: GeminiClient, *, allow_tools=True, system=SYSTEM, timeout_s=20.0):
    return client.generate(
        system=system,
        history=[UserTurn("make it darker")],
        tools=TOOLS,
        allow_tools=allow_tools,
        timeout_s=timeout_s,
    )


def sorts(sdk: FakeSDK) -> list[str]:
    return ["cached" if c.cached_content else "plain" for c in sdk.sent]


# --- created once, reused --------------------------------------------------------


def test_the_cache_is_created_once_and_reused_across_calls_and_clients():
    sdk = FakeSDK()
    client = gemini(sdk)
    replies = [ask(client), ask(client)]
    replies.append(ask(gemini(sdk)))  # another session of the process
    assert len(sdk.created) == 1 and sorts(sdk) == ["cached"] * 3
    made = sdk.created[0]
    assert made.system_instruction == SYSTEM and made.ttl == "1800s"
    assert [d.name for d in made.tools[0].function_declarations] == list(
        atools.TOOL_NAMES
    )
    assert made.tool_config.function_calling_config.mode.value == "AUTO"
    assert SECRET not in (made.display_name or "")
    for config in sdk.sent:  # the prefix is in the cache, not the request
        assert config.cached_content == "cachedContents/c1"
        assert all(getattr(config, name) is None for name in STATIC)
        assert config.thinking_config and config.automatic_function_calling.disable
    assert [r.tokens_cached_in for r in replies] == [2900] * 3
    assert CACHES.made == 1 and CACHES.refused == 0


def test_another_prefix_model_or_key_is_another_cache():
    sdk = FakeSDK()
    ask(gemini(sdk))
    ask(gemini(sdk), system=SYSTEM + " Be brief.")
    ask(gemini(sdk, model="gemini-2.5-pro"))
    other = gemini(sdk)
    other._config = AgentConfig(api_key=SECRET + "x", cache=True)
    ask(other)
    assert len(sdk.created) == 4
    assert {c.display_name for c in sdk.created[:2]} != {sdk.created[0].display_name}


def test_the_summary_call_without_tools_goes_uncached():
    sdk = FakeSDK()
    ask(gemini(sdk), allow_tools=False)
    assert sdk.created == [] and sorts(sdk) == ["plain"]
    assert sdk.sent[0].tool_config.function_calling_config.mode.value == "NONE"


def test_switched_off_it_is_exactly_the_uncached_request():
    assert AgentConfig().cache is False  # the default: unverified live
    sdk = FakeSDK()
    reply = ask(gemini(sdk, cache=False), timeout_s=12.5)
    assert sdk.created == [] and sorts(sdk) == ["plain"]
    config = sdk.sent[0]
    assert config.cached_content is None and config.system_instruction == SYSTEM
    assert len(config.tools[0].function_declarations) == len(TOOLS)
    assert config.tool_config.function_calling_config.mode.value == "AUTO"
    assert config.http_options.timeout == 12500  # the call's own timeout
    assert reply.text == "ok" and reply.tokens_cached_in == 0


def test_the_switch_and_its_knobs_come_from_the_environment(tmp_path):
    def cfg(**env):
        return load_config(env=env, env_file=tmp_path / "missing")

    assert cfg().cache is False and cfg().cache_ttl_s == 1800
    assert cfg(SYNTHLAB_AGENT_CACHE="1").cache is True
    assert cfg(SYNTHLAB_AGENT_CACHE="0").cache is False
    c = cfg(SYNTHLAB_AGENT_CACHE_TTL_S="5", SYNTHLAB_AGENT_CACHE_MIN_TOKENS="4096")
    assert c.cache_ttl_s == 60 and c.cache_min_tokens == 4096  # ttl clamped


def test_a_prefix_under_the_minimum_is_not_offered(caplog):
    size = estimate_tokens(prefix_blob(SYSTEM, TOOLS))
    assert 1024 < size < 4096  # ~2.9k by characters / 4
    sdk = FakeSDK()
    client = gemini(sdk, cache_min_tokens=4096)
    with caplog.at_level(logging.INFO, logger="synthlab.studio.agent"):
        ask(client)
        ask(client)
    assert sdk.created == [] and sorts(sdk) == ["plain", "plain"]
    assert caplog.text.count("agent cache skipped") == 1


# --- expiry ------------------------------------------------------------------------


def test_an_expired_cache_is_made_again(_fresh_caches):
    clock = _fresh_caches
    sdk = FakeSDK()
    client = gemini(sdk, cache_ttl_s=600)
    ask(client)
    clock.now += 600 - aclient.CACHE_EXPIRY_MARGIN_S - 1
    ask(client)
    assert len(sdk.created) == 1  # still inside its life
    clock.now += 2
    ask(client)
    assert len(sdk.created) == 2 and sdk.sent[-1].cached_content.endswith("c2")


def test_the_services_own_expiry_wins_when_it_is_sooner(_fresh_caches):
    from datetime import datetime, timedelta

    clock = _fresh_caches

    class Soon(FakeSDK):
        def create(self, *, model, config):
            made = super().create(model=model, config=config)
            made.expire_time = datetime.now(UTC) + timedelta(seconds=120)
            return made

    sdk = Soon()
    client = gemini(sdk)
    ask(client)
    clock.now += 100  # past 120 s less the margin, long before the 1800 s TTL
    ask(client)
    assert len(sdk.created) == 2


def test_a_cache_the_service_no_longer_has_is_made_again(caplog):
    gone = api_error(403, "CachedContent not found (or permission denied)")
    sdk = FakeSDK(cached=[None, gone])
    client = gemini(sdk)
    ask(client)
    with caplog.at_level(logging.WARNING, logger="synthlab.studio.agent"):
        reply = ask(client)  # the cached call fails: sent again uncached
    assert reply.text == "ok" and sorts(sdk) == ["cached", "cached", "plain"]
    assert "agent cache expired" in caplog.text
    ask(client)
    assert len(sdk.created) == 2 and sorts(sdk)[-1] == "cached"


# --- every cache error falls back ------------------------------------------------


@pytest.mark.parametrize(
    "err",
    [
        api_error(400, "Cached content is too small. min_total_token_count=4096"),
        api_error(404, "models/gemini-flash-latest is not supported for caching"),
        api_error(403, "caching is not enabled for this project"),
    ],
    ids=["too-small", "unsupported-model", "forbidden"],
)
def test_a_refused_create_falls_back_and_is_not_retried_per_call(
    err, caplog, _fresh_caches
):
    clock = _fresh_caches
    sdk = FakeSDK(creates=[err])
    client = gemini(sdk)
    with caplog.at_level(logging.WARNING, logger="synthlab.studio.agent"):
        replies = [ask(client), ask(client), ask(gemini(sdk))]
    assert [r.text for r in replies] == ["ok"] * 3
    assert len(sdk.created) == 1 and sorts(sdk) == ["plain"] * 3
    assert sdk.sent[0].system_instruction == SYSTEM and sdk.sent[0].tools
    assert caplog.text.count("agent cache refused") == 1  # logged once
    clock.now += aclient.CACHE_RETRY_S + 1  # later it may try again
    ask(client)
    assert len(sdk.created) == 2 and sorts(sdk)[-1] == "cached"


TRANSIENT = [
    api_error(429, "quota"),
    api_error(408, "request timeout"),
    errors.ServerError(500, {"error": {"code": 500, "message": "internal"}}),
    errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}}),
    RuntimeError("connection reset"),
]
TRANSIENT_IDS = ["quota", "request-timeout", "server", "unavailable", "network"]


@pytest.mark.parametrize("err", TRANSIENT, ids=TRANSIENT_IDS)
def test_a_transient_create_error_costs_one_call_not_ten_minutes(err, caplog):
    """A 429 or a 5xx while creating the cache marked the prefix refused
    for `CACHE_RETRY_S`: ten minutes of full-price calls for one blip. The
    call that hit it goes uncached; the next one makes the cache."""
    sdk = FakeSDK(creates=[err])
    client = gemini(sdk)
    with caplog.at_level(logging.WARNING, logger="synthlab.studio.agent"):
        replies = [ask(client), ask(client), ask(client)]
    assert [r.text for r in replies] == ["ok"] * 3
    assert sorts(sdk) == ["plain", "cached", "cached"] and len(sdk.created) == 2
    assert CACHES.refused == 0 and CACHES.made == 1
    assert "agent cache refused" not in caplog.text
    assert caplog.text.count("agent cache unavailable") == 1  # logged once


@pytest.mark.parametrize("err", TRANSIENT, ids=TRANSIENT_IDS)
def test_a_transient_error_on_a_cached_call_leaves_the_cache_usable(err):
    """The cached call fails for a reason that is not about the cache: it
    is sent again uncached, once, and the cache is still there for the
    next call — not refused, not made again."""
    sdk = FakeSDK(cached=[err])
    client = gemini(sdk)
    assert [ask(client).text for _ in range(3)] == ["ok"] * 3
    assert sorts(sdk) == ["cached", "plain", "cached", "cached"]
    assert len(sdk.created) == 1 and CACHES.refused == 0


def test_a_cache_without_a_name_is_a_refused_create():
    class Nameless(FakeSDK):
        def create(self, *, model, config):
            self.created.append(config)
            return types.CachedContent()

    sdk = Nameless()
    client = gemini(sdk)
    assert ask(client).text == "ok" and ask(client).text == "ok"
    # not made again per call: each would be a cache nobody can name
    assert sorts(sdk) == ["plain", "plain"] and len(sdk.created) == 1


@pytest.mark.parametrize(
    "err",
    [
        api_error(400, "CachedContent can not be used with tool_config"),
        api_error(403, "permission denied"),
    ],
    ids=["bad-request", "forbidden"],
)
def test_a_refused_cached_call_falls_back_and_stops_caching(err):
    sdk = FakeSDK(cached=[err])
    client = gemini(sdk)
    assert ask(client).text == "ok"
    assert sorts(sdk) == ["cached", "plain"]
    assert ask(client).text == "ok"
    assert len(sdk.created) == 1 and sorts(sdk)[-1] == "plain"  # refused for now


def test_a_brand_new_cache_that_is_already_gone_is_not_remade_every_call():
    gone = api_error(404, "CachedContent not found")
    sdk = FakeSDK(cached=[gone])
    client = gemini(sdk)
    assert ask(client).text == "ok" and ask(client).text == "ok"
    assert len(sdk.created) == 1 and sorts(sdk) == ["cached", "plain", "plain"]


def test_when_the_fallback_fails_too_the_error_is_todays(caplog):
    sdk = FakeSDK(cached=[RuntimeError("boom")], plain=[api_error(429, "quota")])
    with caplog.at_level(logging.DEBUG), pytest.raises(AgentError) as info:
        ask(gemini(sdk))
    assert info.value.kind == "quota"


def test_a_cached_call_that_used_up_the_time_is_not_sent_twice(_fresh_caches):
    clock = _fresh_caches

    def slow():
        clock.now += 20.0  # the whole timeout

    sdk = FakeSDK(cached=[RuntimeError("timed out")], on_cached=slow)
    with pytest.raises(AgentError) as info:
        ask(gemini(sdk), timeout_s=20.0)
    assert info.value.kind == "timeout" and sorts(sdk) == ["cached"]


def test_the_fallback_gets_the_time_that_is_left(_fresh_caches):
    clock = _fresh_caches

    def slowish():
        clock.now += 5.0

    sdk = FakeSDK(cached=[RuntimeError("boom")], on_cached=slowish)
    assert ask(gemini(sdk), timeout_s=20.0).text == "ok"
    assert sdk.sent[-1].http_options.timeout == 15000


def test_the_key_never_reaches_a_cache_log_line(caplog):
    leak = api_error(400, f"API key not valid: {SECRET} (?key={SECRET})")
    sdk = FakeSDK(creates=[leak], cached=[leak])
    with caplog.at_level(logging.DEBUG):
        ask(gemini(sdk))
        ask(gemini(sdk, model="gemini-2.5-pro"))
    assert "agent cache refused" in caplog.text and SECRET not in caplog.text
    assert SECRET not in repr(CACHES._entries)  # a digest, not the key


# --- through a turn ----------------------------------------------------------------


def test_a_turn_succeeds_whatever_the_cache_does(tmp_path):
    """The whole route: every cache call of the turn raises, and the turn
    is the uncached turn — applied, summarised, booked, no error."""

    class Scripted(FakeSDK):
        def generate_content(self, *, model, contents, config):
            self.sent.append(config)
            if config.cached_content:
                raise api_error(500, "cache backend down")
            said = [c for c in contents if c.role == "model"]
            part = (
                types.Part.from_text(text="Closed the filter.")
                if said
                else types.Part(
                    function_call=types.FunctionCall(
                        name="set_params", args={"params": {"cutoff": 500}}
                    )
                )
            )
            resp = response()
            resp.candidates[0].content.parts = [part]
            return resp

    client, svc, _ = agent_client(tmp_path, config=AgentConfig(api_key=SECRET))
    for sdk in (Scripted(creates=[api_error(503, "down")]), Scripted()):
        CACHES.clear()
        svc.agent.client_factory = lambda cfg, sdk=sdk: gemini(sdk)
        svc.agent._client = None
        r = turn(client)
        t = svc.agent.turns[-1]
        assert r.status_code == 200 and not t.error
        assert t.reply == "Closed the filter." and svc.state.patch.cutoff == 500
        assert "plain" in sorts(sdk) and t.tokens == 2 * 3010


def test_a_turn_books_its_cached_tokens_and_the_drawer_shows_them(tmp_path):
    client, svc, _ = agent_client(tmp_path, config=AgentConfig(api_key=SECRET))
    sdk = FakeSDK()
    svc.agent.client_factory = lambda cfg: gemini(sdk)
    turn(client, "make it wider")
    t = svc.agent.turns[-1]
    assert t.tokens == 3010 and t.tokens_cached == 2900
    assert "(2,900 cached)" in client.get("/view/editor").text
    log = json.loads((tmp_path / "agent_log.json").read_text(encoding="utf-8"))
    assert log["turns"][-1]["tokens_cached"] == 2900


# --- the wire, and the eval's dry run ----------------------------------------------


def test_the_requests_the_sdk_would_send(monkeypatch):
    """One layer down: the real google-genai client over a transport that
    answers in place of the network. What `caches.create` and a cached
    `generate_content` put on the wire, as the SDK serialises them."""
    httpx = pytest.importorskip("httpx")
    seen: list[tuple[str, dict, dict]] = []

    def handle(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body, dict(request.headers)))
        if request.url.path.endswith("/cachedContents"):
            return httpx.Response(
                200,
                json={
                    "name": "cachedContents/abc",
                    "usageMetadata": {"totalTokenCount": 2900},
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": "ok"}]}}
                ],
                "usageMetadata": {
                    "promptTokenCount": 3100,
                    "cachedContentTokenCount": 2900,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 3104,
                },
                "modelVersion": "gemini-2.5-flash",
            },
        )

    client = GeminiClient(
        AgentConfig(api_key="not-a-key", cache=True),
        transport=httpx.MockTransport(handle),
    )
    replies = [ask(client), ask(client), ask(client, allow_tools=False)]
    paths = [path for path, _b, _h in seen]
    assert paths[0].endswith("/cachedContents") and len(paths) == 4
    assert all(p.endswith("gemini-flash-latest:generateContent") for p in paths[1:])
    create = seen[0][1]
    assert set(create) == {
        "model",
        "ttl",
        "displayName",
        "systemInstruction",
        "tools",
        "toolConfig",
    }
    assert create["model"] == "models/gemini-flash-latest" and create["ttl"] == "1800s"
    declared = create["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in declared] == list(atools.TOOL_NAMES)
    # (the SDK passes a declaration through in its own snake_case; the API
    # reads either, and the uncached request below carries the same bytes)
    assert "cutoff: 60..16000 Hz" in json.dumps(declared[0]["parameters_json_schema"])
    for _path, body, _h in seen[1:3]:
        assert set(body) == {"contents", "cachedContent", "generationConfig"}
        assert body["cachedContent"] == "cachedContents/abc"
    plain = seen[3][1]  # the no-tools summary: the whole prefix, as before
    assert {"systemInstruction", "tools", "toolConfig"} < set(plain)
    assert "cachedContent" not in plain
    assert plain["tools"] == create["tools"]
    assert [r.tokens_cached_in for r in replies] == [2900] * 3
    assert replies[0].tokens_in == 3100 and replies[0].tokens == 3104


def test_the_evals_dry_run_builds_every_request_offline(capsys, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    pytest.importorskip("httpx")
    path = Path(__file__).resolve().parents[2] / "tools" / "agent_eval.py"
    spec = importlib.util.spec_from_file_location("agent_eval", path)
    agent_eval = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "agent_eval", agent_eval)  # its dataclass
    monkeypatch.setattr(sys, "path", list(sys.path))  # it prepends the repo
    spec.loader.exec_module(agent_eval)
    assert agent_eval.main(["--list", "--only", "1"]) == 0
    assert agent_eval.main(["--dry-run", "--only", "1"]) == 0
    out = capsys.readouterr().out
    assert "cache off: 1 round(s)" in out and "cache on: 1 round(s)" in out
    assert out.count("create cache") == 1 and out.count("cached call") == 2
    assert "ERROR" not in out
    # without a key the real run still refuses, before any call
    assert agent_eval.main(["--only", "1"]) == 2
