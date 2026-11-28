"""synthlab.kits.fetch: a kit fetched file by file, never over the network."""

import email.message
import hashlib
import http.client
import io
import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from synthlab.kits import fetch

BASE = "https://example.test/kit/abc/"
FILES = {
    "a/one.flac": b"1" * 100,
    "a/two.flac": b"2" * 2500,
    "three.txt": b"3" * 7,
}


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    """The retries' waits, recorded and never slept."""
    waits: list[float] = []
    monkeypatch.setattr(fetch, "_sleep", waits.append)
    return waits


def _kit(tmp_path, files=FILES, base=BASE):
    entries = [
        {"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        for p, b in sorted(files.items())
    ]
    mf = tmp_path / "m.json"
    mf.write_text(json.dumps({"files": entries}))
    return fetch.Kit("fake", "Fake", base, str(mf))


def _opener(served, log=None):
    def opener(url):
        if log is not None:
            log.append(url)
        return io.BytesIO(served[url[len(BASE) :]])

    return opener


def test_download_fetches_every_file_and_verifies(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    assert fetch.availability(kit, d)["ready"] is False
    fetch.download(kit, d, opener=_opener(FILES))
    assert fetch.availability(kit, d) == {"ready": True, "megabytes": 0, "reason": ""}
    assert fetch.verified(kit, d)
    for p, b in FILES.items():
        assert (d / p).read_bytes() == b


def test_a_file_with_the_wrong_hash_is_refused_and_deleted(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    served = dict(FILES, **{"a/two.flac": b"X" * 2500})
    with pytest.raises(fetch.KitError, match="a/two.flac"):
        fetch.download(kit, d, opener=_opener(served))
    assert not list(d.rglob("*.part"))
    assert not (d / "a" / "two.flac").exists()
    assert (d / "a" / "one.flac").read_bytes() == FILES["a/one.flac"]


def test_download_resumes_skipping_files_already_right(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    fetch.download(kit, d, opener=_opener(FILES))
    (d / "a" / "two.flac").unlink()
    log = []
    fetch.download(kit, d, opener=_opener(FILES, log))
    assert log == [BASE + "a/two.flac"]


def test_a_server_sending_too_much_is_cut_off(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    served = dict(FILES, **{"three.txt": b"3" * 5000})
    with pytest.raises(fetch.KitError, match="three.txt"):
        fetch.download(kit, d, opener=_opener(served))
    assert not list(d.rglob("*.part"))


def test_cancel_stops_the_download(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    calls = []
    opener = _opener(FILES, calls)
    with pytest.raises(fetch.Cancelled):
        fetch.download(kit, d, cancel=lambda: len(calls) >= 1, opener=opener)
    assert 1 <= len(calls) <= fetch.WORKERS  # the files already in flight
    assert not list(d.rglob("*.part"))


def test_cancel_between_files_keeps_the_finished_one(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    seen = []

    def cancel():
        seen.append(1)
        return len(seen) == 2  # first call: mid-file one; second: after it

    with pytest.raises(fetch.Cancelled):
        fetch.download(kit, d, cancel=cancel, opener=_opener(FILES))
    assert not list(d.rglob("*.part"))


def test_a_dropped_connection_is_a_kit_error(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"

    class Dropped(io.BytesIO):
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"12", 100)

    with pytest.raises(fetch.KitError, match=r"The download of .* failed"):
        fetch.download(kit, d, opener=lambda url: Dropped())
    assert not list(d.rglob("*.part"))


def test_a_short_body_says_it_ended_early(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    served = dict(FILES, **{"a/one.flac": b"1" * 10})
    with pytest.raises(fetch.KitError, match="ended early"):
        fetch.download(kit, d, opener=_opener(served))


def test_stale_part_files_are_swept(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    (d / "a").mkdir(parents=True)
    stale = d / "a" / f"one.flac.{'ab' * 16}.part"
    stale.write_bytes(b"x")
    fetch.download(kit, d, opener=_opener(FILES))
    assert not stale.exists()


def test_the_sweep_leaves_every_other_part_file_alone(tmp_path):
    """`SYNTHLAB_KIT_DIR` may name a folder with other programs' files: only
    this module's own `<file>.<32 hex>.part`, beside a manifest file in a
    manifest folder, is swept."""
    kit, d = _kit(tmp_path), tmp_path / "out"
    (d / "a").mkdir(parents=True)
    (d / "elsewhere").mkdir()
    hexa = "cd" * 16
    others = [
        d / "movie.mkv.part",  # a browser's download
        d / "a" / "one.flac.dead.part",  # not our name
        d / "a" / f"other.flac.{hexa}.part",  # not a manifest file
        d / "elsewhere" / f"one.flac.{hexa}.part",  # not a manifest folder
    ]
    for f in others:
        f.write_bytes(b"x")
    ours = d / f"three.txt.{hexa}.part"
    ours.write_bytes(b"x")
    fetch.download(kit, d, opener=_opener(FILES))
    assert not ours.exists()
    assert all(f.exists() for f in others)


def test_a_part_another_job_holds_is_left_and_the_download_runs(tmp_path, monkeypatch):
    kit, d = _kit(tmp_path), tmp_path / "out"
    (d / "a").mkdir(parents=True)
    held = d / "a" / f"one.flac.{'ef' * 16}.part"
    held.write_bytes(b"x")
    real = fetch.os.unlink

    def unlink(path, *a, **k):
        if str(path) == str(held):
            raise PermissionError(32, "in use by another process")
        return real(path, *a, **k)

    monkeypatch.setattr(fetch.os, "unlink", unlink)
    fetch.download(kit, d, opener=_opener(FILES))
    assert held.exists() and fetch.verified(kit, d)


def test_availability_is_size_only_and_verified_hashes(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    fetch.download(kit, d, opener=_opener(FILES))
    (d / "a" / "two.flac").write_bytes(b"Z" * 2500)  # same size, wrong bytes
    assert fetch.availability(kit, d)["ready"] is True
    assert fetch.verified(kit, d) is False


def test_http_is_refused(tmp_path):
    kit = _kit(tmp_path, base="http://example.test/kit/")
    with pytest.raises(fetch.KitError):
        fetch.download(kit, tmp_path / "out", opener=_opener(FILES))


def test_the_shipped_manifest_pins_the_commit_and_lists_the_samples():
    entries = fetch.manifest()
    assert sum(e["path"].endswith(".flac") for e in entries) == 3840
    assert "407732a548606ac715ad8f3fbac63d9f4df77c0d" in fetch.NAKED.base_url
    assert fetch.NAKED.base_url.startswith("https://")
    assert all(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) for e in entries)
    assert 1.2e9 < sum(e["size"] for e in entries) < 1.4e9


# --- retries: a throttle, a 5xx, a network fault --------------------------------------


def _http_error(url, code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "nope", headers, None)


def _flaky(faults: dict, log: list | None = None):
    """An opener that raises each path's queued faults, then serves it."""
    lock = threading.Lock()

    def opener(url):
        path = url[len(BASE) :]
        with lock:
            if log is not None:
                log.append(path)
            queue = faults.get(path) or []
            fault = queue.pop(0) if queue else None
        if fault is not None:
            raise fault(url) if callable(fault) else fault
        return io.BytesIO(FILES[path])

    return opener


def test_a_429_is_retried_after_its_retry_after(tmp_path, no_waits):
    kit, d = _kit(tmp_path), tmp_path / "out"
    log = []
    faults = {"a/two.flac": [lambda u: _http_error(u, 429, "3")]}
    fetch.download(kit, d, opener=_flaky(faults, log))
    assert fetch.verified(kit, d)
    assert log.count("a/two.flac") == 2
    assert sum(no_waits) == pytest.approx(3.0)


def test_a_timeout_and_a_5xx_are_retried_with_backoff(tmp_path, no_waits):
    kit, d = _kit(tmp_path), tmp_path / "out"
    faults = {
        "a/one.flac": [TimeoutError("timed out"), lambda u: _http_error(u, 503)],
        "three.txt": [urllib.error.URLError("connection reset")],
    }
    fetch.download(kit, d, opener=_flaky(faults))
    assert fetch.verified(kit, d)
    # one.flac waited 1 s then 2 s, three.txt 1 s
    assert sum(no_waits) == pytest.approx(fetch.BACKOFF * 4)


def test_retries_exhausted_is_a_kit_error_naming_the_file(tmp_path, no_waits):
    kit, d = _kit(tmp_path), tmp_path / "out"
    log = []
    faults = {"a/two.flac": [lambda u: _http_error(u, 500)] * 99}
    with pytest.raises(fetch.KitError, match="a/two.flac"):
        fetch.download(kit, d, opener=_flaky(faults, log))
    assert log.count("a/two.flac") == fetch.RETRIES
    assert not list(d.rglob("*.part"))


def test_a_retry_after_past_the_budget_fails_at_once(tmp_path, no_waits):
    kit, d = _kit(tmp_path), tmp_path / "out"
    log = []
    faults = {"three.txt": [lambda u: _http_error(u, 429, "3600")] * 9}
    with pytest.raises(fetch.KitError, match="three.txt"):
        fetch.download(kit, d, opener=_flaky(faults, log))
    assert log.count("three.txt") == 1 and no_waits == []


def test_a_404_is_not_retried(tmp_path):
    kit, d = _kit(tmp_path), tmp_path / "out"
    log = []
    faults = {"three.txt": [lambda u: _http_error(u, 404)] * 9}
    with pytest.raises(fetch.KitError, match="three.txt"):
        fetch.download(kit, d, opener=_flaky(faults, log))
    assert log.count("three.txt") == 1


def test_the_pool_fetches_four_at_once_and_progress_adds_up(tmp_path):
    files = {f"f{i:02d}.bin": bytes([i]) * (100 + i) for i in range(12)}
    kit, d = _kit(tmp_path, files), tmp_path / "out"
    lock, gate = threading.Lock(), threading.Barrier(fetch.WORKERS, timeout=5)
    live, most, seen, entered = [0], [0], [], []

    class Slow(io.BytesIO):
        def __enter__(self):
            with lock:
                live[0] += 1
                most[0] = max(most[0], live[0])
                seen_in = len(entered)
                entered.append(1)
            if seen_in < fetch.WORKERS:
                gate.wait()  # the first four meet: they run at once
            return self

        def __exit__(self, *exc):
            with lock:
                live[0] -= 1
            return super().__exit__(*exc)

    fetch.download(
        kit,
        d,
        progress=lambda done, total: seen.append((done, total)),
        opener=lambda url: Slow(files[url[len(BASE) :]]),
    )
    assert most[0] == fetch.WORKERS and fetch.verified(kit, d)
    total = sum(len(b) for b in files.values())
    assert seen[-1] == (total, total)


def test_cancel_mid_pool_leaves_no_part(tmp_path):
    files = {f"f{i:02d}.bin": bytes([i]) * 5000 for i in range(12)}
    kit, d = _kit(tmp_path, files), tmp_path / "out"
    started = threading.Event()
    flag = threading.Event()

    class Drip(io.BytesIO):
        def read(self, n=-1):
            started.set()
            flag.wait(0.01)
            return super().read(min(n, 100) if n and n > 0 else 100)

    stopper = threading.Timer(0.05, flag.set)
    stopper.start()
    with pytest.raises(fetch.Cancelled):
        fetch.download(
            kit,
            d,
            cancel=flag.is_set,
            opener=lambda url: Drip(files[url[len(BASE) :]]),
        )
    stopper.cancel()
    assert started.is_set()
    assert not list(d.rglob("*.part"))
    assert not fetch.verified(kit, d)


# --- the HTTPS rule ------------------------------------------------------------


def test_a_redirect_off_https_is_refused():
    handler = fetch._HttpsOnly()
    req = urllib.request.Request("https://example.test/kit/a.flac")
    with pytest.raises(fetch.KitError, match="off HTTPS"):
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://example.test/kit/a.flac"
        )
    ok = handler.redirect_request(
        req, None, 302, "Found", {}, "https://cdn.example.test/a.flac"
    )
    assert ok.full_url == "https://cdn.example.test/a.flac"


def test_the_opener_refuses_a_plain_http_url():
    with pytest.raises(fetch.KitError, match="HTTPS only"):
        fetch._open("http://example.test/kit/a.flac")
