"""A project with its audio (`studio/projectzip.py`): GET /project.zip
carries every sample and clip the project references, and /project/import
takes the bundle back — strictly, never overwriting a clip, never
installing a sample under a name its audio doesn't hash to."""

from __future__ import annotations

import io
import json
import shutil
import warnings
import wave
import zipfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.core import SR
from synthlab.studio import limits, projectzip
from synthlab.studio.clips import save_clip
from synthlab.studio.server import create_app
from synthlab.synth import Patch

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _tone(hz: float, seconds: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = np.sin(2 * np.pi * hz * t) * 0.5
    return np.column_stack([x, x])


def _client(path: Path) -> TestClient:
    return TestClient(create_app(data_dir=path))


def _rich(client, root: Path) -> dict:
    """A session referencing audio every way it can: a sampler main sound
    (made from the clip "vox", which itself is not referenced), a sampler
    in slot A, a clip on the loop's lane ("riser"), a drum one-shot
    ("hit"), and a song made from the loop (its take and drums track
    carry them too). Plus a clip and a sample nothing references."""
    s = client.app.state.studio
    save_clip(root, "vox", _tone(220))
    save_clip(root, "riser", _tone(330), meta={"bpm": 120, "source": "bounce"})
    save_clip(root, "hit", _tone(880, 0.1))
    save_clip(root, "stray", _tone(990))
    client.post("/fragment/sampler/clip", data={"name": "vox"}, headers=HX)
    vox = s.patch.sampler_sample
    slot = sampler.save(s.sample_dir, _tone(440))
    s.slot_a = Patch(osc1="sampler", sampler_sample=slot)
    sampler.save(s.sample_dir, _tone(550))  # nobody's
    s.arrangement = replace(
        s.arrangement, clips=[{"clip": "riser", "at_bar": 0, "gain": 1.0}]
    )
    client.post("/fragment/drums/sample", data={"voice": "kick", "clip.kick": "hit"})
    client.post("/song/from-loop", data={"name": "a"})
    s.save()
    return {"vox": vox, "slot": slot}


def _import(client, content: bytes, name: str = "p.zip"):
    return client.post(
        "/project/import",
        files={"file": (name, content, "application/zip")},
        follow_redirects=False,
    )


def _rezip(content: bytes, add=(), drop=()) -> bytes:
    """The bundle `content` with entries dropped and (name, bytes) added."""
    src = zipfile.ZipFile(io.BytesIO(content))
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on a duplicate name
        with zipfile.ZipFile(buf, "w") as z:
            for info in src.infolist():
                if info.filename not in drop:
                    z.writestr(info, src.read(info))
            for name, data in add:
                z.writestr(name, data)
    return buf.getvalue()


def _wav(channels=2, width=2, rate=SR, frames=441) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\1" * (frames * channels * width))
    return buf.getvalue()


def _files(root: Path) -> set[str]:
    return {
        str(p.relative_to(root)).replace("\\", "/")
        for sub in ("clips", "samples")
        if (root / sub).exists()
        for p in (root / sub).iterdir()
    }


@pytest.fixture
def exported(tmp_path):
    """(the bundle, the ids, the source studio's dir)."""
    src = tmp_path / "src"
    with _client(src) as client:
        ids = _rich(client, src)
        r = client.get("/project.zip")
        assert r.status_code == 200
        return r, ids, src, client.get("/project.json").content


def test_the_bundle_is_the_project_and_the_audio_it_uses(exported):
    r, ids, src, project_json = exported
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].endswith('.synthlab.zip"')
    z = zipfile.ZipFile(io.BytesIO(r.content))
    # project.json is exactly what /project.json serves
    assert z.read("project.json") == project_json
    assert set(z.namelist()) == {
        "project.json",
        f"samples/{ids['vox']}.wav",
        f"samples/{ids['slot']}.wav",
        "clips/riser.wav",
        "clips/riser.meta.json",
        "clips/hit.wav",
        "clips/hit.meta.json",
    }  # not "vox" (a sampler keeps its snapshot), "stray" or the lone sample
    assert z.read("clips/riser.wav") == (src / "clips" / "riser.wav").read_bytes()
    x = sampler.from_wav_bytes(z.read(f"samples/{ids['vox']}.wav"))
    assert sampler.sample_id(x) == ids["vox"]


def test_a_bundle_is_the_same_bytes_whenever_it_is_built(tmp_path, monkeypatch):
    """The entries carried the wall clock (`writestr(name, ...)`), so two
    exports of one session differed from one 2 s tick to the next."""
    from synthlab.studio.files import ZIP_EPOCH
    from tests.studio.test_export_formats import TickingClock

    src = tmp_path / "src"
    with _client(src) as client:
        _rich(client, src)
        payload = client.app.state.studio.project_payload()
        monkeypatch.setattr(zipfile, "time", TickingClock())
        first = projectzip.build(payload, src)
        assert projectzip.build(payload, src) == first
    infos = zipfile.ZipFile(io.BytesIO(first)).infolist()
    assert all(i.date_time == ZIP_EPOCH for i in infos)
    kinds = {i.filename.rsplit(".", 1)[1]: i.compress_type for i in infos}
    # audio stored, text deflated: as before
    assert kinds == {"json": zipfile.ZIP_DEFLATED, "wav": zipfile.ZIP_STORED}


def test_a_bundle_restores_the_session_and_its_audio_elsewhere(exported, tmp_path):
    r, ids, src, _ = exported
    dst = tmp_path / "dst"
    with _client(dst) as client:
        imported = _import(client, r.content)
        assert imported.status_code == 303
        s = client.app.state.studio
        assert s.patch.sampler_sample == ids["vox"]
        assert s.slot_a.sampler_sample == ids["slot"]
        assert s.arrangement.clips[0]["clip"] == "riser"
        assert _files(dst) == {
            f"samples/{ids['vox']}.wav",
            f"samples/{ids['slot']}.wav",
            "clips/riser.wav",
            "clips/riser.meta.json",
            "clips/hit.wav",
            "clips/hit.meta.json",
        }
        for name in ("riser.wav", "hit.wav"):
            assert (dst / "clips" / name).read_bytes() == (
                src / "clips" / name
            ).read_bytes()
        meta = json.loads((dst / "clips" / "riser.meta.json").read_text("utf-8"))
        assert meta["bpm"] == 120 and meta["name"] == "riser"
        on_disk = (dst / "samples" / f"{ids['vox']}.wav").read_bytes()
        assert sampler.sample_id(sampler.from_wav_bytes(on_disk)) == ids["vox"]
        # the sound plays
        assert np.abs(client.app.state.svc.render("editor").audio()).max() > 0.01
        # htmx gets the reload, as for a project.json
        again = client.post(
            "/project/import",
            files={"file": ("p.zip", r.content, "application/zip")},
            headers=HX,
        )
        assert again.status_code == 200 and again.headers["HX-Refresh"] == "true"
        assert _files(dst) == {  # the same audio again: nothing new
            f"samples/{ids['vox']}.wav",
            f"samples/{ids['slot']}.wav",
            "clips/riser.wav",
            "clips/riser.meta.json",
            "clips/hit.wav",
            "clips/hit.meta.json",
        }


def test_a_taken_clip_name_is_never_overwritten(exported, tmp_path):
    """ "riser" here is different audio: the project's comes in as "riser 2"
    and every reference follows; "hit" here is the same bytes: reused."""
    r, _, src, _ = exported
    dst = tmp_path / "dst"
    save_clip(dst, "riser", _tone(123))
    mine = (dst / "clips" / "riser.wav").read_bytes()
    (dst / "clips").mkdir(exist_ok=True)
    shutil.copy(src / "clips" / "hit.wav", dst / "clips" / "hit.wav")
    with _client(dst) as client:
        assert _import(client, r.content).status_code == 303
        s = client.app.state.studio
        assert (dst / "clips" / "riser.wav").read_bytes() == mine
        assert (dst / "clips" / "riser 2.wav").read_bytes() == (
            src / "clips" / "riser.wav"
        ).read_bytes()
        meta = json.loads((dst / "clips" / "riser 2.meta.json").read_text("utf-8"))
        assert meta["name"] == "riser 2"
        assert not (dst / "clips" / "hit 2.wav").exists()
        # the references moved with it — the lane, the song's take, the
        # drums track's slot (still "hit")
        assert s.arrangement.clips[0]["clip"] == "riser 2"
        take = s.song["sections"][0]["audio"][0]["clip"]
        assert take == "riser 2"
        drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
        assert drums["drum_samples"]["kick"] == "hit"
    # and it was saved so
    with _client(dst) as client:
        assert client.app.state.studio.arrangement.clips[0]["clip"] == "riser 2"


def test_a_free_name_steers_clear_of_the_bundles_own_names(exported, tmp_path):
    r, _, _, _ = exported
    dst = tmp_path / "dst"
    save_clip(dst, "riser", _tone(123))
    content = _rezip(r.content, add=[("clips/riser 2.wav", _wav())])
    with _client(dst) as client:
        assert _import(client, content).status_code == 303
        assert client.app.state.studio.arrangement.clips[0]["clip"] == "riser 3"
        assert (dst / "clips" / "riser 2.wav").read_bytes() == _wav()


def test_a_sample_that_doesnt_match_its_name_is_skipped(exported, tmp_path):
    r, ids, _, _ = exported
    wrong = sampler.wav_bytes(sampler.register(_tone(777)))
    name = f"samples/{ids['vox']}.wav"
    content = _rezip(r.content, drop={name}, add=[(name, wrong)])
    dst = tmp_path / "dst"
    with _client(dst) as client:
        assert _import(client, content).status_code == 303
        assert client.app.state.studio.patch.sampler_sample == ids["vox"]
    assert not (dst / "samples" / f"{ids['vox']}.wav").exists()
    assert (dst / "samples" / f"{ids['slot']}.wav").exists()


def _bad_variants(content: bytes) -> dict[str, bytes]:
    wav = _wav()
    riser_meta = "clips/riser.meta.json"
    return {
        "traversal": _rezip(content, add=[("../evil.wav", wav)]),
        "traversal in samples": _rezip(content, add=[("samples/../../x.wav", wav)]),
        "traversal in clips": _rezip(content, add=[("clips/../evil.wav", wav)]),
        "backslash traversal": _rezip(content, add=[("clips\\..\\evil.wav", wav)]),
        "absolute": _rezip(content, add=[("/etc/evil.wav", wav)]),
        "drive": _rezip(content, add=[("C:/evil.wav", wav)]),
        "nested clip": _rezip(content, add=[("clips/sub/evil.wav", wav)]),
        "other file": _rezip(content, add=[("notes.txt", b"hi")]),
        "directory": _rezip(content, add=[("samples/", b"")]),
        "uppercase id": _rezip(content, add=[("samples/ABCDEF0123456789.wav", wav)]),
        "short id": _rezip(content, add=[("samples/abc.wav", wav)]),
        "unsafe clip name": _rezip(content, add=[("clips/CON.wav", wav)]),
        "duplicate": _rezip(content, add=[("clips/riser.wav", wav)]),
        "duplicate by case": _rezip(content, add=[("clips/RISER.wav", wav)]),
        "meta without clip": _rezip(content, add=[("clips/lone.meta.json", b"{}")]),
        "meta not an object": _rezip(
            content, drop={riser_meta}, add=[(riser_meta, b"[1, 2]")]
        ),
        "meta not json": _rezip(content, drop={riser_meta}, add=[(riser_meta, b"{")]),
        "notes without clip": _rezip(content, add=[("takenotes/lone.json", b"{}")]),
        "notes not json": _rezip(content, add=[("takenotes/riser.json", b"{")]),
        "nested notes": _rezip(content, add=[("takenotes/a/riser.json", b"{}")]),
        "clip not a wav": _rezip(content, add=[("clips/junk.wav", b"RIFFjunk")]),
        "8-bit clip": _rezip(content, add=[("clips/lofi.wav", _wav(width=1))]),
        "no project": _rezip(content, drop={"project.json"}),
        "not a project": _rezip(
            content,
            drop={"project.json"},
            add=[("project.json", json.dumps({"format": "nope"}).encode())],
        ),
        "project not json": _rezip(
            content, drop={"project.json"}, add=[("project.json", b"{nope")]
        ),
        "truncated zip": content[: len(content) // 2],
    }


def test_a_bad_bundle_is_refused_whole_and_writes_nothing(exported, tmp_path):
    r, _, _, _ = exported
    dst = tmp_path / "dst"
    with _client(dst) as client:
        s = client.app.state.studio
        bpm = s.arrangement.bpm = 91.0
        for why, content in _bad_variants(r.content).items():
            got = _import(client, content)
            assert got.status_code == 422, why
            assert "toast" in got.text, why
            assert _files(dst) == set(), why
            assert s.arrangement.bpm == bpm and s.patch.osc1 != "sampler", why
    assert not (tmp_path / "evil.wav").exists()


def test_the_bundle_caps(exported, tmp_path, monkeypatch):
    r, _, _, _ = exported
    dst = tmp_path / "dst"
    with _client(dst) as client:
        monkeypatch.setattr(limits, "MAX_PROJECT_ZIP_MEMBERS", 3)
        assert _import(client, r.content).status_code == 422
        monkeypatch.setattr(limits, "MAX_PROJECT_ZIP_MEMBERS", 512)
        monkeypatch.setattr(limits, "MAX_PROJECT_ZIP_UNCOMPRESSED", 10_000)
        assert _import(client, r.content).status_code == 422
        monkeypatch.setattr(limits, "MAX_PROJECT_ZIP_UNCOMPRESSED", 200_000_000)
        # one entry past what an entry of its kind can be: a deflated
        # "sample" of zeros far longer than 30 s
        big = b"\0" * (projectzip._SAMPLE_BYTES + 1)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "project.json",
                zipfile.ZipFile(io.BytesIO(r.content)).read("project.json"),
            )
            z.writestr("samples/0123456789abcdef.wav", big)
        assert len(buf.getvalue()) < 100_000
        assert _import(client, buf.getvalue()).status_code == 422
        assert _files(dst) == set()


def test_a_bare_project_json_keeps_its_own_cap(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        body = client.get("/project.json").content
        monkeypatch.setattr(limits, "MAX_PROJECT_UPLOAD_BYTES", len(body) - 1)
        r = client.post(
            "/project/import", files={"file": ("p.json", body, "application/json")}
        )
        assert r.status_code == 413
        monkeypatch.setattr(limits, "MAX_PROJECT_UPLOAD_BYTES", len(body))
        r = client.post(
            "/project/import",
            files={"file": ("p.json", body, "application/json")},
            follow_redirects=False,
        )
        assert r.status_code == 303


def test_the_dock_offers_both_exports_and_imports_either(tmp_path):
    html = _client(tmp_path).get("/view/editor").text
    assert 'href="/project.json"' in html and 'href="/project.zip"' in html
    assert "Export project + audio" in html
    accept = html.split('name="file" accept="', 1)[1].split('"', 1)[0]
    assert ".json" in accept and ".zip" in accept


def test_sample_refs_reach_every_patch_in_the_payload():
    a, b, c = "0123456789abcdef", "fedcba9876543210", "00000000000000aa"
    payload = {
        "patch": {"sampler_sample": a},
        "layers": [{"patch": {"sampler_sample": b}}],
        "song": {"tracks": [{"patch": {"sampler_sample": c}}]},
        "slot_a": {"sampler_sample": "../../etc"},  # not an id: ignored
        "slot_b": None,
    }
    assert projectzip.sample_refs(payload) == {a, b, c}
    # a multi-sample instrument: every zone's sample travels too
    d, e = "00000000000000bb", "00000000000000cc"
    payload["slot_b"] = {"sampler_zones": f"{d},0,59;{e},60,127;../x,0,1"}
    assert projectzip.sample_refs(payload) == {a, b, c, d, e}


# -- review round: a clip's meta is typed, and a failed install still lands ---


def test_a_meta_of_the_wrong_types_is_dropped_not_installed(exported, tmp_path):
    """`"bpm": "fast"` passed as "a scalar", was installed, and the shelf's
    `c.bpm | round | int` then failed the import's own answer and every
    page after it."""
    r, _, _, _ = exported
    evil = {"bpm": "fast", "bars": True, "sr": "44100", "source": 5, "note": "kept"}
    content = _rezip(
        r.content,
        drop={"clips/riser.meta.json"},
        add=[("clips/riser.meta.json", json.dumps(evil))],
    )
    dst = tmp_path / "dst"
    with _client(dst) as client:
        r = client.post(
            "/project/import",
            files={"file": ("p.zip", content, "application/zip")},
            headers=HX,
        )
        assert r.status_code == 200 and r.headers["HX-Refresh"] == "true"
        for path in ("/", "/view/perform"):
            assert client.get(path).status_code == 200
    meta = json.loads((dst / "clips" / "riser.meta.json").read_text())
    assert meta == {"note": "kept", "name": "riser"}


def test_a_generated_takes_settings_survive_the_bundle(exported):
    """A riser's meta holds the settings that made it (`transition`), which
    is how the same settings place the same clip again: not a scalar, and
    kept all the same. Anything deeper is not."""
    r, _, _, _ = exported
    spec = {
        "kind": "riser",
        "direction": "up",
        "bars": 2,
        "brightness": 0.6,
        "tail": 0.4,
        "bpm": 124.0,
    }
    meta = {"source": "transition", "bpm": 124.0, "bars": 2, "transition": spec}
    nested = {"transition": {"kind": {"deep": []}}, "other": {"a": 1}}
    content = _rezip(
        r.content,
        drop={"clips/riser.meta.json", "clips/hit.meta.json"},
        add=[
            ("clips/riser.meta.json", json.dumps(meta)),
            ("clips/hit.meta.json", json.dumps(nested)),
        ],
    )
    bundle = projectzip.read(content)
    assert bundle.clips["riser"][1] == meta
    assert bundle.clips["hit"][1] == {}


TAKE = {"source": "keys", "kept": True, "bpm": 120.0, "bars": 1}
NOTES = [[0.0, 1.0, 33, 0.8], [2.0, 1.0, 36, 1.0]]


def test_a_keys_takes_notes_travel_in_their_sidecar(exported, tmp_path):
    """A placed clip that is a keys take carries its notes' sidecar
    (`takenotes/<name>.json`), read back through `keytakes.clean_doc` — the
    one reader — and installed beside the clip under the name it lands on."""
    r, _, src, _ = exported
    from synthlab.studio import keytakes
    from synthlab.studio.clips import write_meta

    write_meta(src, "riser", TAKE)
    keytakes.write_notes(src, "riser", {"notes": NOTES, "raw": NOTES})
    with _client(src) as client:
        content = client.get("/project.zip").content
    names = zipfile.ZipFile(io.BytesIO(content)).namelist()
    assert "takenotes/riser.json" in names
    hostile = json.dumps({"notes": [*NOTES, [0, 1, 999, 1], "x"], "fixes": [1, "ok"]})
    bundle = projectzip.read(
        _rezip(
            content,
            drop={"takenotes/riser.json"},
            add=[("takenotes/riser.json", hostile)],
        )
    )
    assert bundle.notes["riser"] == {"notes": NOTES, "fixes": ["ok"]}
    dst = tmp_path / "dst"
    with _client(dst) as client:
        save_clip(dst, "riser", _tone(100))  # taken here: it lands as "riser 2"
        assert _import(client, content).status_code in (200, 303)
    assert (dst / "clips" / "riser 2.wav").exists()
    assert keytakes.read_notes(dst, "riser 2") == {"notes": NOTES, "raw": NOTES}
    assert keytakes.read_notes(dst, "riser") == {"notes": []}  # this studio's own


def test_an_older_takes_notes_in_its_meta_move_into_a_sidecar(exported):
    r, _, _, _ = exported
    meta = {**TAKE, "notes": [*NOTES, "junk"]}
    content = _rezip(
        r.content,
        drop={"clips/riser.meta.json"},
        add=[("clips/riser.meta.json", json.dumps(meta))],
    )
    bundle = projectzip.read(content)
    assert "notes" not in bundle.clips["riser"][1]
    assert bundle.notes["riser"] == {"notes": NOTES}


def test_an_install_that_fails_still_resets_the_caches(exported, tmp_path, monkeypatch):
    """Only OSError was caught: anything else left the imported session
    behind a 500, playing from the old session's caches."""
    r, _, _, _ = exported

    def broken(bundle, state):
        raise RuntimeError("install went wrong")

    monkeypatch.setattr(projectzip, "install", broken)
    client = TestClient(create_app(data_dir=tmp_path / "dst"))
    svc = client.app.state.svc
    before = svc.cache, svc.notes, svc.stems
    assert _import(client, r.content).status_code == 303
    assert svc.state.arrangement.clips[0]["clip"] == "riser"  # it loaded
    after = svc.cache, svc.notes, svc.stems
    assert all(a is not b for a, b in zip(before, after, strict=True))


def test_audio_that_arrives_after_its_project_is_heard(exported, tmp_path, monkeypatch):
    """project.json first (no audio: the sampler plays silence), then the
    bundle. The import reset the loop and stem caches, but not the notes
    behind them (`render.NOTE_MEMO`) nor the song's remembered levels:
    all keyed by a patch's content, where a sample is only its id. The
    sound stayed silent, sample and all, until the studio restarted."""
    from synthlab.studio import render

    r, ids, _src, project_json = exported
    # another machine: no sample store, nothing rendered yet
    monkeypatch.setattr(sampler, "_DIRS", [])
    monkeypatch.setattr(sampler, "_MEM", sampler._Cache(sampler._CACHE_BYTES))
    render.NOTE_MEMO.clear()
    with _client(tmp_path / "dst") as client:
        svc = client.app.state.svc
        assert _import(client, project_json, "p.json").status_code == 303
        assert sampler.get(ids["vox"]) is None
        assert np.abs(svc.render("editor").audio()).max() == 0.0
        svc.song_gain(*svc.song_snapshot())  # the song remembers its levels
        memos = svc.song_levels, svc.song_memo
        assert _import(client, r.content).status_code == 303
        assert sampler.get(ids["vox"]) is not None
        assert np.abs(svc.render("editor").audio()).max() > 0.01
        assert svc.song_levels is not memos[0] and svc.song_memo is not memos[1]
