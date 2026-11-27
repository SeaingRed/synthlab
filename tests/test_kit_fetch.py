"""synthlab.kits.fetch: a kit fetched file by file, never over the network."""

import hashlib
import http.client
import io
import json
import re

import pytest

from synthlab.kits import fetch

BASE = "https://example.test/kit/abc/"
FILES = {
    "a/one.flac": b"1" * 100,
    "a/two.flac": b"2" * 2500,
    "three.txt": b"3" * 7,
}


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
    assert len(calls) == 1
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

    with pytest.raises(fetch.KitError, match="a/one.flac"):
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
    stale = d / "a" / "one.flac.dead.part"
    stale.write_bytes(b"x")
    fetch.download(kit, d, opener=_opener(FILES))
    assert not stale.exists()


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
