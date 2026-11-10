"""The model behind the sound agent, behind one small interface.

`loop.py` talks to a `ModelClient`: `generate(system, history, tools,
allow_tools, timeout_s) -> Reply`. The history is SDK-neutral (`UserTurn`,
`Reply`, `ToolTurn`), so the tests drive the loop with a scripted fake and
never touch the network, and the Gemini adapter is the only code that knows
the SDK's types.

`GeminiClient` wraps `google-genai` (the optional `agent` extra, imported
lazily: the studio runs without it). Automatic function calling is off —
the studio applies every call itself, under its own lock and validation —
and a model turn is handed back to the API verbatim (`Reply.raw`), which
keeps Gemini 3's thought signatures attached to their function calls. Each
call gets its own HTTP timeout (the SDK counts milliseconds) and one retry
on 429/5xx; the loop's turn deadline bounds the rest.

Errors come out as `AgentError` with a short, *scrubbed* message: no SDK
exception text reaches a toast or a log line without `config.scrub`.

The static prefix (system instruction + tool declarations, the same on
every call) can ride an explicit context cache (`PrefixCaches`,
`config.cache`, off by default): `caches.create` once per (key, model,
prefix), then calls name it as `cached_content` and send only the history.
It FAILS SAFE: whatever a cache call raises — a model that cannot cache, a
prefix under the provider's minimum, an expired cache — the same call goes
out again uncached, exactly as it would have without the feature.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, Protocol

from .config import AgentConfig, scrub

log = logging.getLogger("synthlab.studio.agent")

CACHE_RETRY_S = 600.0  # after a refused cache: uncached calls for this long
CACHE_CREATE_TIMEOUT_S = 8.0  # of the call's own timeout, never on top of it
CACHE_EXPIRY_MARGIN_S = 30.0  # a cache this close to expiring is not used
CHARS_PER_TOKEN = 4.0  # the offline estimate (no tokenizer ships offline)


class AgentError(Exception):
    """A turn that could not complete. `message` is safe to show."""

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.kind = kind  # "unavailable" | "timeout" | "quota" | "auth" | "error"


@dataclass
class Call:
    id: str | None
    name: str
    args: dict


@dataclass
class UserTurn:
    text: str
    audio: bytes | None = None  # a short WAV of the sound


@dataclass
class Reply:
    text: str
    calls: list[Call] = field(default_factory=list)
    tokens: int = 0
    raw: Any = None  # the SDK's own content, sent back verbatim
    # the bill's split of `tokens` (the money estimate): input (of which
    # audio), and output with the thinking included; 0/0 when the API gave
    # no split, which leaves the reply unpriced
    tokens_in: int = 0
    tokens_audio_in: int = 0
    tokens_out: int = 0
    # the part of `tokens_in` read from a context cache (ours, or Gemini's
    # implicit one); 0 when the API reported none
    tokens_cached_in: int = 0
    model: str = ""  # the model that answered (an alias resolved), if known


def reply_cost(reply: Reply, price) -> float | None:
    """US dollars for one reply at `price` (`config.Price`), or None when
    the price or the token split is unknown. Cached input is charged at
    `price.cached_in`, or at the full input rate while the table has none
    (it is cheaper in fact: the estimate errs high). Creating a cache and
    keeping it (billed per hour) are not in it."""
    if price is None or (reply.tokens_in + reply.tokens_out == 0 and reply.tokens):
        return None
    audio = min(reply.tokens_audio_in, reply.tokens_in)
    cached = min(reply.tokens_cached_in, reply.tokens_in - audio)  # text only
    rate_audio = price.input if price.audio_in is None else price.audio_in
    rate_cached = price.input if price.cached_in is None else price.cached_in
    return (
        (reply.tokens_in - audio - cached) * price.input
        + cached * rate_cached
        + audio * rate_audio
        + reply.tokens_out * price.output
    ) / 1e6


@dataclass
class ToolTurn:
    results: list[tuple[Call, dict]]
    text: str = ""  # measured features after the calls, the self-check
    audio: bytes | None = None


class ModelClient(Protocol):
    def generate(
        self,
        *,
        system: str,
        history: list,
        tools: list[dict],
        allow_tools: bool,
        timeout_s: float,
    ) -> Reply: ...


def usage_split(usage) -> dict:
    """`Reply`'s token split from a response's `usage_metadata`: input is
    the prompt plus any tool-use prompt, audio its AUDIO modality share,
    output the candidates plus the thoughts (billed as output). Cached is
    `cached_content_token_count`, which the prompt count already includes
    (the SDK's field description says so), so it never adds to the total."""

    def n(name: str) -> int:
        try:
            return max(0, int(getattr(usage, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    audio = 0
    for detail in getattr(usage, "prompt_tokens_details", None) or []:
        modality = str(getattr(detail, "modality", "") or "").upper()
        if modality.endswith("AUDIO"):  # "AUDIO" or "MediaModality.AUDIO"
            try:
                audio += max(0, int(getattr(detail, "token_count", 0) or 0))
            except (TypeError, ValueError):
                pass
    tokens_in = n("prompt_token_count") + n("tool_use_prompt_token_count")
    return {
        "tokens_in": tokens_in,
        "tokens_audio_in": min(audio, tokens_in),
        "tokens_out": n("candidates_token_count") + n("thoughts_token_count"),
        "tokens_cached_in": min(n("cached_content_token_count"), tokens_in),
    }


def estimate_tokens(text: str) -> int:
    """Roughly how many tokens `text` is: characters / 4. An estimate —
    the SDK's local tokenizer needs sentencepiece and a download."""
    return round(len(text) / CHARS_PER_TOKEN)


def prefix_blob(system: str, tools: list[dict]) -> str:
    """The static prefix as one canonical string: what its hash and its
    size estimate are taken from."""
    return json.dumps([system, tools], sort_keys=True, separators=(",", ":"))


@dataclass
class _Cached:
    name: str | None  # the cache's resource name; None: refused, see `until`
    until: float  # monotonic: usable until then / not retried before then


class PrefixCaches:
    """The process's context caches, one per (key, model, prefix hash):
    every `GeminiClient` of the process shares them, so a second studio
    session or a rebuilt client reuses the cache the first one made.

    `lookup` answers a cache name or None ("send it uncached"). An entry
    past its expiry is recreated; `forget` drops one the service no longer
    knows (expired early, deleted); `refuse` marks a prefix uncacheable for
    `CACHE_RETRY_S`, so a model or a prefix the service will not cache
    costs one failed call, not one per request. Only the API saying no to
    the cache or the request does that (`GeminiClient.REFUSAL_CODES`): a
    transient error leaves the registry as it was."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[tuple, _Cached] = {}
        self._warned: set[tuple] = set()
        self.made = self.refused = 0  # since the process began (the eval)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._warned.clear()
            self.made = self.refused = 0

    def get(self, key: tuple) -> _Cached | None:
        """The live entry for `key`: a usable cache, or a refusal still in
        force. An expired one of either sort is dropped."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and monotonic() >= entry.until:
                del self._entries[key]
                entry = None
            return entry

    def put(self, key: tuple, name: str, ttl_s: float) -> None:
        with self._lock:
            self._entries[key] = _Cached(name, monotonic() + ttl_s)
            self.made += 1

    def forget(self, key: tuple) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def refuse(self, key: tuple) -> None:
        with self._lock:
            self._entries[key] = _Cached(None, monotonic() + CACHE_RETRY_S)
            self.refused += 1

    def first(self, key: tuple, what: str) -> bool:
        """True the first time (`key`, `what`) is asked: "logged once"."""
        with self._lock:
            new = (key, what) not in self._warned
            self._warned.add((key, what))
            return new


CACHES = PrefixCaches()


def sdk_available() -> bool:
    try:
        import google.genai  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "not installed"
        return False
    return True


class GeminiClient:
    """`ModelClient` over google-genai. Holds the key only inside the SDK
    client; `repr` never shows it."""

    RETRY_CODES = (429, 500, 502, 503, 504)
    # a cache call's answers that are about the cache or the request itself:
    # the prefix is too small or the model cannot be cached (400), caching
    # is not allowed here (403), the model or the cache is not there (404).
    # Only these mark a prefix refused for `CACHE_RETRY_S`. Everything else
    # (429, 408, 5xx, a dropped connection, a timeout) is transient: that
    # one call goes uncached and the cache stays as it was — a single 429
    # used to cost ten minutes of full-price calls.
    REFUSAL_CODES = (400, 403, 404)

    def __init__(self, config: AgentConfig, transport=None):
        if not config.has_key:
            raise AgentError("no API key", kind="unavailable")
        from google import genai
        from google.genai import types

        self._types = types
        self._config = config
        # `transport`: an httpx transport that answers in place of the
        # network (tools/agent_eval.py --dry-run, the wire-shape test)
        extra = {"client_args": {"transport": transport}} if transport else {}
        self._client = genai.Client(
            api_key=config.api_key,
            http_options=types.HttpOptions(
                timeout=int(config.timeout_s * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=2,
                    initial_delay=1.0,
                    max_delay=4.0,
                    http_status_codes=list(self.RETRY_CODES),
                ),
                **extra,
            ),
        )

    def __repr__(self) -> str:
        return f"GeminiClient(model={self._config.model!r})"

    # -- history -> SDK contents ------------------------------------------
    def _contents(self, history: list) -> list:
        t = self._types
        out = []
        for item in history:
            if isinstance(item, UserTurn):
                parts = [t.Part.from_text(text=item.text)]
                if item.audio:
                    parts.append(
                        t.Part.from_bytes(data=item.audio, mime_type="audio/wav")
                    )
                out.append(t.Content(role="user", parts=parts))
            elif isinstance(item, Reply):
                if item.raw is not None:
                    out.append(item.raw)
                else:  # a reply built by hand (tests, a replayed log)
                    parts = [t.Part.from_text(text=item.text)] if item.text else []
                    parts += [
                        t.Part(
                            function_call=t.FunctionCall(
                                id=c.id, name=c.name, args=c.args
                            )
                        )
                        for c in item.calls
                    ]
                    out.append(t.Content(role="model", parts=parts))
            elif isinstance(item, ToolTurn):
                parts = [
                    t.Part(
                        function_response=t.FunctionResponse(
                            id=call.id, name=call.name, response=result
                        )
                    )
                    for call, result in item.results
                ]
                if item.text:
                    parts.append(t.Part.from_text(text=item.text))
                if item.audio:
                    parts.append(
                        t.Part.from_bytes(data=item.audio, mime_type="audio/wav")
                    )
                out.append(t.Content(role="user", parts=parts))
        return out

    # -- the static prefix's cache -------------------------------------------
    def _cache_key(self, system: str, tools: list[dict]) -> tuple:
        """(key digest, model, prefix hash). The digest only tells two keys
        apart in memory (a cache belongs to the project that made it); it
        is never logged."""
        digest = hashlib.sha256((self._config.api_key or "").encode()).hexdigest()
        prefix = hashlib.sha256(prefix_blob(system, tools).encode()).hexdigest()
        return (digest[:16], self._config.model, prefix[:16])

    def _cache_failed(
        self, key: tuple, err: Exception, *, gone: bool = False, refused: bool = False
    ):
        """A cache call raised: the caller sends the request uncached.
        `gone` (the service no longer has a cache we made earlier): drop
        it, the next call makes another. `refused`, or an answer that is
        about the cache or the request (`REFUSAL_CODES`): no caching for
        this prefix for `CACHE_RETRY_S`. Anything else is transient: this
        call goes uncached and nothing is recorded, so the next call uses
        the cache (or makes it). Logged once per kind, scrubbed."""
        refused = refused or getattr(err, "code", None) in self.REFUSAL_CODES
        if gone:
            CACHES.forget(key)
        elif refused:
            CACHES.refuse(key)
        what = "expired" if gone else "refused" if refused else "unavailable"
        if CACHES.first(key, what):
            msg = scrub(getattr(err, "message", None) or err, self._config.secrets())
            log.warning(
                "agent cache %s for %s (%s %s %s): sending uncached",
                what,
                self._config.model,
                type(err).__name__,
                getattr(err, "code", None),
                msg[:200],
            )

    def _cache_name(
        self, key: tuple, system: str, tools: list[dict], static: dict, timeout_s: float
    ) -> tuple[str | None, bool]:
        """(the prefix's cache, whether it was made just now). No name:
        send the request uncached — the prefix is refused for now, too
        small, or its cache could not be created."""
        t, cfg = self._types, self._config
        entry = CACHES.get(key)
        if entry is not None:
            return entry.name, False
        size = estimate_tokens(prefix_blob(system, tools))
        if size < cfg.cache_min_tokens:
            if CACHES.first(key, "small"):
                log.info(
                    "agent cache skipped: the prefix is ~%d tokens (chars/4), "
                    "under the %d minimum",
                    size,
                    cfg.cache_min_tokens,
                )
            return None, False
        try:
            made = self._client.caches.create(
                model=cfg.model,
                config=t.CreateCachedContentConfig(
                    **static,
                    ttl=f"{int(cfg.cache_ttl_s)}s",
                    display_name=f"synthlab-agent-{key[2]}",
                    http_options=t.HttpOptions(
                        timeout=max(
                            1000, int(min(timeout_s, CACHE_CREATE_TIMEOUT_S) * 1000)
                        )
                    ),
                ),
            )
        except Exception as err:  # noqa: BLE001 - any failure means "uncached"
            self._cache_failed(key, err)
            return None, False
        name = getattr(made, "name", None)
        if not name:
            # the service made something we cannot name: asking again would
            # make another per call, so this one is a refusal
            err = ValueError("the cache came back without a name")
            self._cache_failed(key, err, refused=True)
            return None, False
        life = float(cfg.cache_ttl_s)
        expires = getattr(made, "expire_time", None)
        if isinstance(expires, datetime) and expires.tzinfo is not None:
            server = (expires - datetime.now(expires.tzinfo)).total_seconds()
            if server > 0:  # the service's own word, when it is sooner
                life = min(life, server)
        CACHES.put(key, name, max(1.0, life - CACHE_EXPIRY_MARGIN_S))
        return name, True

    @staticmethod
    def _is_gone(err: Exception) -> bool:
        """A cached call refused because the cache is no longer there (the
        API answers 404, or 403 "CachedContent not found (or permission
        denied)" once one has expired)."""
        code = getattr(err, "code", None)
        text = str(getattr(err, "message", None) or err).lower()
        return code in (403, 404) and "cache" in text

    def generate(
        self,
        *,
        system: str,
        history: list,
        tools: list[dict],
        allow_tools: bool,
        timeout_s: float,
    ) -> Reply:
        t = self._types
        cfg = self._config
        decls = [
            t.FunctionDeclaration(
                name=d["name"],
                description=d["description"],
                parameters_json_schema=d["parameters"],
            )
            for d in tools
        ]
        # the static prefix: in the request, or in the cache the request
        # names (the API refuses one that names a cache AND carries these)
        static = {
            "system_instruction": system,
            "tools": [t.Tool(function_declarations=decls)],
            "tool_config": t.ToolConfig(
                function_calling_config=t.FunctionCallingConfig(
                    mode="AUTO" if allow_tools else "NONE"
                )
            ),
        }
        contents = self._contents(history)
        started = monotonic()

        def call(left_s: float, **prefix):
            return self._client.models.generate_content(
                model=cfg.model,
                contents=contents,
                config=t.GenerateContentConfig(
                    automatic_function_calling=t.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                    thinking_config=t.ThinkingConfig(thinking_level="low"),
                    http_options=t.HttpOptions(timeout=max(1000, int(left_s * 1000))),
                    **prefix,
                ),
            )

        resp = None
        left = timeout_s
        # the cache holds the tools-allowed prefix; the summary call of a
        # turn that used every round (mode NONE) is rare and goes uncached
        if cfg.cache and allow_tools:
            key = self._cache_key(system, tools)
            name, fresh = self._cache_name(key, system, tools, static, timeout_s)
            left = timeout_s - (monotonic() - started)
            if name is not None:
                try:
                    resp = call(left, cached_content=name)
                except Exception as err:  # noqa: BLE001 - falls back uncached
                    # a cache made a moment ago that is already "gone" would
                    # be gone next time too: refuse (its 403 / 404 does),
                    # don't recreate per call. A transient error (429, 5xx,
                    # the network) leaves the cache for the next call.
                    self._cache_failed(key, err, gone=self._is_gone(err) and not fresh)
                    left = timeout_s - (monotonic() - started)
                    if left < 1.0:  # it timed out: no time for a second go
                        raise self._map_error(err) from None
        if resp is None:
            try:
                resp = call(left, **static)
            except Exception as err:  # noqa: BLE001 - mapped, scrubbed, re-raised
                raise self._map_error(err) from None
        return self._reply(resp)

    def _reply(self, resp) -> Reply:
        usage = getattr(resp, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0)
        split = usage_split(usage)
        cands = getattr(resp, "candidates", None) or []
        content = cands[0].content if cands and cands[0].content else None
        calls = [
            Call(getattr(fc, "id", None), fc.name or "", dict(fc.args or {}))
            for fc in (resp.function_calls or [])
        ]
        text = ""
        if content is not None:
            text = "".join(
                p.text
                for p in (content.parts or [])
                if getattr(p, "text", None) and not getattr(p, "thought", False)
            )
        if content is None and not calls:
            reason = getattr(cands[0], "finish_reason", None) if cands else None
            feedback = getattr(resp, "prompt_feedback", None)
            why = reason or getattr(feedback, "block_reason", None) or "empty reply"
            raise AgentError(f"the model returned nothing ({why})")
        return Reply(
            text=text.strip(),
            calls=calls,
            tokens=tokens,
            raw=content,
            model=str(getattr(resp, "model_version", None) or ""),
            **split,
        )

    def _map_error(self, err: Exception) -> AgentError:
        secrets = self._config.secrets()
        name = type(err).__name__
        code = getattr(err, "code", None)
        msg = scrub(getattr(err, "message", None) or err, secrets)[:200]
        log.warning("gemini call failed: %s %s %s", name, code, msg)
        low = f"{name} {msg}".lower()
        if "timeout" in low or "timed out" in low:
            return AgentError("the model took too long to answer", kind="timeout")
        if code in (401, 403) or "api key" in low or "api_key" in low:
            return AgentError(
                "Gemini refused the API key: check GEMINI_API_KEY in .env", kind="auth"
            )
        if code == 429:
            return AgentError(
                "Gemini rate limit or quota reached: try again shortly", kind="quota"
            )
        if code == 404:
            return AgentError(
                f"model {self._config.model!r} not found: check GEMINI_MODEL"
            )
        if isinstance(code, int) and code >= 500:
            return AgentError("Gemini is unavailable right now: try again")
        if isinstance(err, OSError) or "connect" in low:
            return AgentError("couldn't reach Gemini (offline?)", kind="timeout")
        return AgentError(f"model call failed ({name}{f' {code}' if code else ''})")
