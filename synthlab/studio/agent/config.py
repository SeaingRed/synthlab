"""The sound agent's configuration, and the one place its secret is held.

The Gemini key lives in the checkout's own `.env` (git-ignored), never in
state.json, a template, a response or a log. `load_config()` reads that file
with a tiny parser (no python-dotenv dependency) and lets real environment
variables win, so a deployment can inject the key without a file.

The file is resolved relative to the *package*, not the cwd: the checkout
the running code came from (`<repo>/.env`). A studio started from a temp
directory still finds it; another checkout without one finds none.

Everything that could echo text coming back from the network — an SDK
exception, an HTTP error body — goes through `scrub()` before it reaches a
toast, a log line or a test assertion.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# <repo>/synthlab/studio/agent/config.py -> <repo>/.env
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

DEFAULT_MODEL = "gemini-flash-latest"
# what a session may spend before the drawer stops sending (visible counter)
DEFAULT_TOKEN_BUDGET = 250_000
DEFAULT_TURN_BUDGET = 40
DEFAULT_TIMEOUT_S = 30.0  # one model call
DEFAULT_TURN_DEADLINE_S = 55.0  # every call of one turn together
MAX_TOOL_ROUNDS = 3  # model calls that may change the sound, per turn
# Explicit context caching of the static prefix (system instruction + tool
# declarations; `client.PrefixCaches`). OFF by default: the request shape
# is the SDK's (google-genai 2.25, `caches.create` / `cached_content`), but
# nothing here has run against the live API, and a cache's storage is
# billed by the hour at a rate `PRICES` does not list.
DEFAULT_CACHE = False
DEFAULT_CACHE_TTL_S = 1800  # a cache outlives a pause between turns
# The smallest prefix worth offering to the cache, in estimated tokens
# (characters / 4). The SDK states no minimum, so this is UNVERIFIED:
# 1024 for Flash models and 4096 for Pro is recalled from Google's caching
# page, not read for this change. The trimmed prefix is ~2.9k. Too low only
# costs one refused create (then uncached calls for `client.CACHE_RETRY_S`).
DEFAULT_CACHE_MIN_TOKENS = 1024


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens, paid tier, standard (not batch,
    prompts <= 200k tokens — a turn is far under that). `audio_in` is the
    audio-input rate where the model has its own (None: the input rate).
    `cached_in` is the rate of input tokens read from a context cache
    (implicit or explicit); None, as in every row of `PRICES` (the table's
    source page was not re-read for it): the input rate, an over-estimate.
    `until`/`then`: a price that changes on a date (the first day it no
    longer applies, and the price from then on)."""

    input: float
    output: float
    audio_in: float | None = None
    cached_in: float | None = None
    until: date | None = None
    then: Price | None = None

    def on(self, day: date) -> Price:
        if self.until is not None and self.then is not None and day >= self.until:
            return self.then.on(day)
        return self


# The money estimate beside the token count ("≈ $0.004"). From
# https://ai.google.dev/gemini-api/docs/pricing, last updated 2026-09-24:
# check it again when a model is added. Only ids that name ONE price belong
# here. `gemini-flash-latest` (the default GEMINI_MODEL) is an alias Google
# hot-swaps with every release, so a reply is priced by the model that
# actually answered (the response's `model_version`), and a model not
# listed gets no estimate rather than a wrong one. Output is billed with
# the model's thinking tokens included.
_FLASH_2027 = Price(1.50, 7.50)
PRICES: dict[str, Price] = {
    "gemini-3.8-flash": Price(0.75, 3.75, until=date(2027, 1, 1), then=_FLASH_2027),
    "gemini-3.7-flash": Price(0.75, 3.75, until=date(2027, 1, 1), then=_FLASH_2027),
    "gemini-3.5-flash": Price(1.50, 9.00),
    "gemini-3.5-flash-lite": Price(0.30, 2.50),
    "gemini-3.1-flash-lite": Price(0.25, 1.50, audio_in=0.50),
    "gemini-3.1-pro-preview": Price(2.00, 12.00),
    "gemini-2.5-pro": Price(1.25, 10.00),
    "gemini-2.5-flash": Price(0.30, 2.50, audio_in=1.00),
    "gemini-2.5-flash-lite": Price(0.10, 0.40, audio_in=0.30),
}
# a numbered build of a listed model is that model's price
# ("gemini-2.5-flash-001", "models/gemini-3.8-flash"); a *different*
# variant ("-lite", "-image", "-tts") or a dated preview is not
_BUILD = re.compile(r"-\d{3}$")


def price_for(model: str | None, day: date | None = None) -> Price | None:
    """The listed price of `model` on `day` (today), or None: unknown."""
    name = str(model or "").strip().lower().removeprefix("models/")
    found = PRICES.get(name)
    if found is None:
        base = _BUILD.sub("", name)
        found = PRICES.get(base) if base != name else None
    return found.on(day or date.today()) if found else None


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_dotenv(text: str) -> dict[str, str]:
    """`KEY=value` lines; `#` comments, blank lines and `export ` prefixes
    allowed; a value may be single- or double-quoted (quotes stripped, no
    escapes or interpolation). Anything else is ignored, never an error."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:  # an unquoted trailing comment
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def read_env_file(path: Path) -> dict[str, str]:
    try:
        return parse_dotenv(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return {}


def _num(raw: str | None, default: float, lo: float, hi: float) -> float:
    try:
        v = float(raw) if raw is not None else default
    except ValueError:
        return default
    return min(hi, max(lo, v)) if v == v else default


def _flag(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no", "")


@dataclass(frozen=True)
class AgentConfig:
    """What the agent runs with. `api_key` is excluded from `repr` so a
    config that lands in a log line or a traceback's locals prints without
    it."""

    api_key: str | None = field(default=None, repr=False)
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    turn_deadline_s: float = DEFAULT_TURN_DEADLINE_S
    token_budget: int = DEFAULT_TOKEN_BUDGET
    turn_budget: int = DEFAULT_TURN_BUDGET
    audio: bool = True  # attach a ~2 s render of the sound to the request
    audio_stereo: bool = True  # ...in stereo when the sound is (else mono)
    cache: bool = DEFAULT_CACHE  # cache the static prefix (fails safe)
    cache_ttl_s: int = DEFAULT_CACHE_TTL_S
    cache_min_tokens: int = DEFAULT_CACHE_MIN_TOKENS
    max_rounds: int = MAX_TOOL_ROUNDS
    source: str = "none"  # where the key came from: "env" | ".env" | "none"
    # SYNTHLAB_AGENT_PRICE_IN / _OUT ($ per million tokens), both set: the
    # price of every model instead of `PRICES` (audio in at the input rate;
    # cached input too, unless SYNTHLAB_AGENT_PRICE_CACHED gives its own)
    price_override: Price | None = None

    def price(self, model: str | None) -> Price | None:
        return self.price_override or price_for(model)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def secrets(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()


def load_config(
    env: Mapping[str, str] | None = None, env_file: Path | None = None
) -> AgentConfig:
    """The config from `env` (default `os.environ`) over `env_file`
    (default `ENV_FILE`, read at call time so a test can point it
    elsewhere). Environment variables win over the file."""
    env = os.environ if env is None else env
    file_vals = read_env_file(ENV_FILE if env_file is None else env_file)

    def get(name: str) -> str | None:
        v = env.get(name)
        if v is not None and v.strip():
            return v.strip()
        v = file_vals.get(name)
        return v.strip() if v is not None and v.strip() else None

    key = get("GEMINI_API_KEY")
    source = "none"
    if key:
        source = "env" if (env.get("GEMINI_API_KEY") or "").strip() else ".env"
    price_in = _num(get("SYNTHLAB_AGENT_PRICE_IN"), -1.0, -1.0, 1e4)
    price_out = _num(get("SYNTHLAB_AGENT_PRICE_OUT"), -1.0, -1.0, 1e4)
    price_cached = _num(get("SYNTHLAB_AGENT_PRICE_CACHED"), -1.0, -1.0, 1e4)
    override = None
    if min(price_in, price_out) >= 0:
        cached = price_cached if price_cached >= 0 else None
        override = Price(price_in, price_out, cached_in=cached)
    return AgentConfig(
        api_key=key,
        model=get("GEMINI_MODEL") or DEFAULT_MODEL,
        timeout_s=_num(get("SYNTHLAB_AGENT_TIMEOUT_S"), DEFAULT_TIMEOUT_S, 5, 120),
        turn_deadline_s=_num(
            get("SYNTHLAB_AGENT_TURN_DEADLINE_S"), DEFAULT_TURN_DEADLINE_S, 10, 170
        ),
        token_budget=int(
            _num(get("SYNTHLAB_AGENT_TOKEN_BUDGET"), DEFAULT_TOKEN_BUDGET, 1e3, 1e8)
        ),
        turn_budget=int(
            _num(get("SYNTHLAB_AGENT_TURN_BUDGET"), DEFAULT_TURN_BUDGET, 1, 1e4)
        ),
        audio=_flag(get("SYNTHLAB_AGENT_AUDIO"), True),
        audio_stereo=_flag(get("SYNTHLAB_AGENT_AUDIO_STEREO"), True),
        cache=_flag(get("SYNTHLAB_AGENT_CACHE"), DEFAULT_CACHE),
        cache_ttl_s=int(
            _num(get("SYNTHLAB_AGENT_CACHE_TTL_S"), DEFAULT_CACHE_TTL_S, 60, 86400)
        ),
        cache_min_tokens=int(
            _num(
                get("SYNTHLAB_AGENT_CACHE_MIN_TOKENS"),
                DEFAULT_CACHE_MIN_TOKENS,
                0,
                1e7,
            )
        ),
        source=source,
        price_override=override,
    )


# a key-shaped token (Google API keys are "AIza" + 35 url-safe chars), and
# any `key=...` / `x-goog-api-key: ...` fragment an error might echo
_KEYLIKE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
_KEYPARAM = re.compile(
    r"(?i)((?:api[_-]?key|x-goog-api-key|key)\s*[=:]\s*)[^\s&\"',;]+"
)


def scrub(text: object, secrets: tuple[str, ...] = ()) -> str:
    """`text` as a string with every secret, anything key-shaped and any
    `key=...` parameter replaced by `[redacted]`. Applied to everything
    that came back from the network before it is shown or logged."""
    out = str(text)
    for s in secrets:
        if s:
            out = out.replace(s, "[redacted]")
    out = _KEYLIKE.sub("[redacted]", out)
    return _KEYPARAM.sub(r"\1[redacted]", out)
