"""HTTP boundary for a local studio: the server trusts the machine it runs
on, not every web page that machine's browser visits.

Also bounds request body size before anything downstream — routing,
`request.form()`'s multipart parser, or a route's own `read_capped` — ever
sees it. Starlette's multipart parser has no size cap of its own
for file parts (`max_part_size` only bounds non-file form fields), so an
unbounded POST would otherwise be fully spooled to a temp file before any
route-level check ran."""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from . import limits

# Loopback names only. Starlette's TestClient calls itself "testserver":
# the suite adds that name itself (tests/conftest.py), production never
# accepts it.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOKEN_COOKIE = "synthlab_token"
# script-src 'wasm-unsafe-eval' lets the page compile WebAssembly (the live
# voice's DSP module, static/voice-dsp.wasm, compiled by livefx.js) and
# nothing else: no eval(), no new Function(), no string timers — those
# still need 'unsafe-eval', which this policy never grants. Without it
# WebAssembly.compile throws and the live voice falls back to its JS DSP.
CSP = (
    "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'wasm-unsafe-eval'; "
    "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)
# Framing: no other origin may frame the studio — the Voice view
# holds microphone permission. Its own pages may: the Song page docks the
# Synths page beside the playlist (`fragments/song_dock.html`).
FRAME_OPTIONS = "SAMEORIGIN"

# Routes whose upload cap is not the generic MAX_UPLOAD_BYTES ceiling,
# named by the limits.py attribute to look up (read fresh per request, so a
# test's monkeypatch of the attribute still takes effect).
_PATH_UPLOAD_LIMITS = {
    "/voice/take": "MAX_TAKE_UPLOAD_BYTES",
    # the larger of its two forms: a bundle; a bare project.json is held to
    # MAX_PROJECT_UPLOAD_BYTES by the route itself (routes/export.py)
    "/project/import": "MAX_PROJECT_ZIP_BYTES",
    "/fragment/pattern/import": "MAX_MIDI_UPLOAD_BYTES",
    # a whole song for the Sample Lab (studio/sources.py)
    "/lab/upload": "MAX_SOURCE_UPLOAD_BYTES",
    # a drawn wavetable: a few hundred numbers as text (studio/wavetables)
    "/fragment/wavetable/draw": "MAX_WT_DRAW_BYTES",
    # a phrase said to the loop coach (🎙): a few seconds of mono WAV
    "/fragment/coach/talk": "MAX_COACH_TALK_BYTES",
}


class _BodyTooLarge(Exception):
    """Internal signal: the running `receive()` byte count passed the cap."""


class BodySizeLimit:
    """Pure ASGI middleware: rejects an oversized request body before the
    app underneath — routing, `request.form()`'s multipart parser, or a
    route's own `read_capped` — ever sees it.

    `@app.middleware("http")` (BaseHTTPMiddleware) can't easily wrap
    `receive`, so this is a plain ASGI class registered with
    `app.add_middleware` instead. Two legs:

    - a declared ``Content-Length`` over the cap is refused immediately,
      before `receive()` is ever called — the handler never runs;
    - an undeclared/chunked body is bounded by wrapping `receive` and
      counting bytes as they arrive, raising once the running total passes
      the cap.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _limit_for(path: str) -> int:
        attr = _PATH_UPLOAD_LIMITS.get(path)
        return getattr(limits, attr) if attr else limits.MAX_UPLOAD_BYTES

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                over = int(declared) > limit
            except ValueError:
                over = False
            if over:
                await PlainTextResponse("request body too large", status_code=413)(
                    scope, receive, send
                )
                return

        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body") or b"")
                if total > limit:
                    raise _BodyTooLarge()
            return message

        # If the wrapped app already started sending a response before the
        # cap tripped, it's too late to send our own 413 — let the original
        # exception propagate rather than violate the ASGI send-once rule.
        started = False

        async def guarded_send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if started:
                raise
            await PlainTextResponse("request body too large", status_code=413)(
                scope, receive, send
            )


def _hostname(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


def token_cookie(request: Request) -> str:
    """The share cookie's name, per port: a browser keeps cookies by host
    alone, so two shared studios on one machine (8420 and 8421) wrote one
    `synthlab_token` and each refused the other's link from then on."""
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return f"{TOKEN_COOKIE}_{port}"


def _token_match(candidate: str, expected: str) -> bool:
    """hmac.compare_digest raises TypeError on a non-ASCII str (it only
    accepts bytes or ASCII-only str), so a cookie or `?token=é` used to
    surface as a 500 instead of a 401. Comparing the utf-8 encoded bytes
    keeps constant-time behavior and never raises on the input."""
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _foreign_read(request: Request):
    """The answer to a GET/HEAD another site started, or None to serve it.

    Reads were waved through unchecked, and some of them do work or change
    state: `/view/breed` on an empty board breeds a litter and spends the
    starred parents, `/fx/ir.wav` renders under the studio lock,
    `/export/stems.zip` renders the whole song — each reachable from any
    web page as an `<img>` or a `fetch`. So a read whose `Sec-Fetch-Site`
    is cross-site or same-site is refused unless it is a top-level
    navigation (a link someone followed: the share link from a chat app),
    and that navigation only ever lands on `/` — any other path is
    redirected there, so a link on another site can't start a render or a
    generation either. No `Sec-Fetch-Site` (curl, tests, old browsers) and
    `none` (the address bar, a bookmark) are untouched."""
    if request.headers.get("sec-fetch-site") not in ("cross-site", "same-site"):
        return None
    top_level = (
        request.headers.get("sec-fetch-mode") == "navigate"
        and request.headers.get("sec-fetch-dest") == "document"
    )
    if not top_level:
        return PlainTextResponse("cross-site request refused", status_code=403)
    if request.url.path != "/":
        return RedirectResponse("/", 303)
    return None


def install(app, allowed_hosts=None, share_token: str | None = None) -> None:
    hosts = frozenset(allowed_hosts or LOCAL_HOSTS)

    # Added before `boundary` (below) so `boundary` — registered next —
    # stays the outermost layer: a bad host/origin is rejected before this
    # middleware even looks at the body.
    app.add_middleware(BodySizeLimit)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        # With a share token set, the Host check is skipped on purpose: the
        # LAN IP a phone/laptop uses to reach this machine varies, and the
        # token itself is the auth boundary in that mode.
        if share_token is None and _hostname(host) not in hosts:
            return PlainTextResponse("bad host", status_code=400)
        if share_token is not None:
            q = request.query_params.get("token")
            if q and _token_match(q, share_token):
                resp = RedirectResponse(request.url.remove_query_params("token"), 303)
                resp.set_cookie(
                    token_cookie(request), share_token, httponly=True, samesite="lax"
                )
                return resp
            got = request.cookies.get(token_cookie(request), "")
            if not _token_match(got, share_token):
                return PlainTextResponse(
                    "open the link printed at startup", status_code=401
                )
        if request.method not in SAFE_METHODS:
            site = request.headers.get("sec-fetch-site")
            if site is not None and site not in ("same-origin", "none"):
                return PlainTextResponse("cross-site request refused", status_code=403)
            origin = request.headers.get("origin")
            # "null" is sent by sandboxed iframes, file:// pages and some
            # privacy modes; no studio flow legitimately sends it, so it is
            # treated as a mismatch rather than exempted.
            if origin and (origin == "null" or urlsplit(origin).netloc != host):
                return PlainTextResponse(
                    "cross-origin request refused", status_code=403
                )
        elif (refusal := _foreign_read(request)) is not None:
            return refusal
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", FRAME_OPTIONS)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
