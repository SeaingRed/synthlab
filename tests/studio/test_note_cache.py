"""Key traffic must never evict a loop preview.

`/note/*` WAVs used to share the 50-entry RenderCache with `/preview/{key}`
renders, and `/preview` only serves what is still cached. Warming one octave
is 12 renders, so a few octaves of keys could push the loop the browser was
about to fetch out of the cache, and that fetch 404ed. Held keys made it
worse: each `?hold=1` render carries 3 s more audio. Key and pad one-shots
now live in their own `NoteCache`, and both caches are bounded in bytes as
well as entries, so a long song's section renders can't blow up memory
either.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.lru import LRU
from synthlab.studio.render import NoteCache, RenderCache, RenderResult
from synthlab.studio.server import create_app

ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_lru_evicts_by_bytes_as_well_as_entries():
    c: LRU[bytes] = LRU(max_entries=100, max_bytes=10, sizeof=len)
    c.put("a", b"xxxx")
    c.put("b", b"xxxx")
    assert c.stats()["bytes"] == 8
    c.put("c", b"xxxx")  # 12 > 10: the oldest goes
    assert c.get("a") is None and c.get("b") is not None
    assert c.stats()["bytes"] == 8 and c.stats()["evictions"] == 1
    c.put("b", b"x")  # replacing a key re-counts it, not adds to it
    assert c.stats()["bytes"] == 5


def test_lru_keeps_a_single_oversized_entry():
    # an item bigger than the whole budget is still served once: evicting it
    # on insert would 404 the very request that rendered it
    c: LRU[bytes] = LRU(max_entries=100, max_bytes=4, sizeof=len)
    c.put("big", b"x" * 10)
    assert c.get("big") is not None


def test_both_caches_are_byte_bounded():
    assert RenderCache().max_bytes and NoteCache().max_bytes
    r = RenderCache(max_entries=100, max_bytes=10)
    r.put(RenderResult(key="k1", wav=b"x" * 6, analysis={}))
    r.put(RenderResult(key="k2", wav=b"x" * 6, analysis={}))
    assert r.get("k1") is None and r.get("k2") is not None


def test_a_burst_of_keys_never_evicts_the_loop_preview(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/preview", data={"view": "editor"}, headers=ORIGIN)
    url = r.headers["hx-trigger"].split('"url": "')[1].split('"')[0]
    assert client.get(url).status_code == 200
    # well past the old 50-entry shared budget
    for octave in (2, 3, 4):
        for name in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A"):
            pitch = name.replace("#", "%23") + str(octave)
            assert client.get(f"/note/{pitch}.wav?hold=1").status_code == 200
    assert client.get(url).status_code == 200
    svc = client.app.state.svc
    assert svc.notes.stats()["entries"] == 30
    assert all(not k.startswith("note:") for k in svc.cache.keys())
