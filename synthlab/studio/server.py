"""Compatibility shim: the studio used to live here, as every route inside
one `create_app()` closure.

The app is assembled in `app.py`, its routes live one router per area in
`routes/`, the shared helpers in `service/` (`StudioService`) and the
concurrency plumbing in `routing.py`. This module keeps the old import
path working — `synthlab.studio.server:create_app`, and the
`synthlab-studio` entry point (`main`, `_parse_args`) — so tools, tests
and pyproject need not move.
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path

from . import vendor
from .app import STUDIO_DIR, create_app, default_data_dir
from .routing import (
    ClipUpload,
    ProjectUpload,
    RawForm,
    StrForm,
    StudioRoute,
    TakeUpload,
    Upload,
    lock_free,
    read_capped,
)

__all__ = [
    "STUDIO_DIR",
    "ClipUpload",
    "ProjectUpload",
    "RawForm",
    "StrForm",
    "StudioRoute",
    "TakeUpload",
    "Upload",
    "create_app",
    "default_data_dir",
    "lock_free",
    "main",
    "make_server",
    "read_capped",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="synthlab-studio")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="session state directory (default: ./studio_data) — point at a "
        "scratch dir to keep test pokes out of your real state",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="bind address (default: 127.0.0.1, or 0.0.0.0 with --share). "
        "Anything but a loopback address is served as --share is: behind "
        "a printed token",
    )
    parser.add_argument("--port", type=int, default=8420, help="bind port")
    parser.add_argument(
        "--share",
        action="store_true",
        help="bind all interfaces and require a printed token (good until "
        "the studio stops) — for trusted LANs only",
    )
    args = parser.parse_args(argv)
    if args.host is None:
        args.host = "0.0.0.0" if args.share else "127.0.0.1"
    return args


def _lan_ip() -> str:
    """Best-effort LAN IP: a UDP "connect" to a public address never sends
    a packet but makes the OS pick the outbound interface, so the socket's
    local address is this machine's LAN IP rather than a loopback/VPN one.
    Falls back to the hostname if that fails (no route, offline, ...)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()


def _is_loopback(host: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _share_token(args: argparse.Namespace) -> str | None:
    """The token a launch is served behind, or None for a loopback bind.

    Not `--share` alone: `--host 0.0.0.0` without it started with no token,
    and the only gate left was the Host allowlist — a header the client
    writes (`curl -H "Host: localhost" http://<ip>:8420/project.json`
    answered, POSTs too). Whatever is reachable from another machine gets
    a token; only a loopback bind goes without."""
    if not args.share and _is_loopback(args.host):
        return None
    import secrets

    return secrets.token_urlsafe(16)


def _urls(args: argparse.Namespace, token: str | None) -> tuple[str, str | None]:
    """(the URL this machine's browser opens, the token link to print for
    the other device — None on a loopback bind)."""

    def url(host: str) -> str:
        host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{host}:{args.port}"

    if token is None:
        return url("127.0.0.1"), None
    everywhere = args.host in ("0.0.0.0", "::")
    # a wildcard bind answers on loopback too; one address answers only there
    here = "127.0.0.1" if everywhere or _is_loopback(args.host) else args.host
    there = _lan_ip() if everywhere else args.host
    return f"{url(here)}/?token={token}", f"{url(there)}/?token={token}"


def make_server(app, **config):
    """The uvicorn server for `app`, which stops when asked to. uvicorn
    waits for every open connection to finish before it runs the app's
    shutdown, and an open `/events` stream never finishes by itself: one
    page left open would hold Ctrl+C until a second one. So the signal
    handler closes the event bus first (`EventBus.close`: lock-free, made
    to be called from here), each stream ends, and the usual graceful
    shutdown goes through."""
    import uvicorn

    class StudioServer(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            app.state.svc.events.close()
            super().handle_exit(sig, frame)

    return StudioServer(uvicorn.Config(app, **config))


def main() -> None:
    """`uv run synthlab-studio` — serve the studio and open the browser."""
    import threading
    import webbrowser

    args = _parse_args()
    token = _share_token(args)
    browser_url, link = _urls(args, token)
    if link:
        print(f"open {link} on the other device")
    # GSAP is not in the repository (its licence is not open source): the
    # first launch fetches it from npm, pinned and checked (vendor.py)
    vendor.ensure()
    threading.Timer(0.8, webbrowser.open, args=(browser_url,)).start()
    app = create_app(args.data_dir, share_token=token)
    # Ctrl+C: uvicorn has already shut down cleanly by the time asyncio.run
    # re-raises the interrupt (Python 3.14 does), so it is not an error
    with contextlib.suppress(KeyboardInterrupt):
        make_server(app, host=args.host, port=args.port).run()
