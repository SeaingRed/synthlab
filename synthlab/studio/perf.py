"""Request/render statistics behind /debug/stats.

Everything here is a plain counter — recording must stay cheap enough to
sit on every request, including the slider-drag storm this exists to
diagnose. Snapshots are computed on read, never on write. One lock:
requests are recorded on the event loop, renders in the threadpool, and
/debug/stats reads from another thread still.
"""

from __future__ import annotations

import threading

UNMATCHED = "(unmatched)"  # every path no route answers, as one row


def route_key(scope) -> str:
    """What a request is counted under: the path template of the route
    that matched it (set on the ASGI scope by the router), or UNMATCHED."""
    return getattr(scope.get("route"), "path", None) or UNMATCHED


class PerfStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # "POST /fragment/patch" -> {count, total_ms, max_ms}. The path is
        # the matched route's template ("/preview/{key}.wav"), never the
        # URL: keyed on that, every preview key, clip name and 404 probe
        # added an entry that was never evicted. So the table is bounded
        # by the route list (plus UNMATCHED per method).
        self.requests: dict[str, dict[str, float]] = {}
        self.renders = {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "last_ms": 0.0}

    def record_request(self, method: str, path: str, ms: float) -> None:
        with self._lock:
            row = self.requests.setdefault(
                f"{method} {path}", {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            row["count"] += 1
            row["total_ms"] += ms
            row["max_ms"] = max(row["max_ms"], ms)

    def record_render(self, ms: float) -> None:
        with self._lock:
            r = self.renders
            r["count"] += 1
            r["total_ms"] += ms
            r["max_ms"] = max(r["max_ms"], ms)
            r["last_ms"] = ms

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        requests = {
            key: {
                "count": row["count"],
                "avg_ms": round(row["total_ms"] / row["count"], 1),
                "max_ms": round(row["max_ms"], 1),
            }
            for key, row in self.requests.items()
        }
        renders = dict(self.renders)
        if renders["count"]:
            renders["avg_ms"] = round(renders["total_ms"] / renders["count"], 1)
        for k in ("total_ms", "max_ms", "last_ms"):
            renders[k] = round(renders[k], 1)
        return {"requests": requests, "renders": renders}
