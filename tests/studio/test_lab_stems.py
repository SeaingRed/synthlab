"""The Sample Lab's separation job (`labjobs.py`), its stem cache and the
stem lanes — with a fake separator behind the real one's interface
(`lab_helpers.FilterBank`) and no model file or network."""

from __future__ import annotations

import io
import json
import re
import threading
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.audio import read_pcm16, wav_bytes
from synthlab.core import SR
from synthlab.studio import lab, labmake, limits, sources
from synthlab.studio.clips import list_clips, load_clip
from synthlab.studio.server import create_app
from tests.songs import pair_song
from tests.studio.lab_helpers import (
    LAB,
    BassSplit,
    SixBands,
    fake_separation,
    separate,
    song_wav,
    source_id,
    upload,
)


@pytest.fixture(scope="module")
def wav() -> bytes:
    return song_wav(bars=4)


@pytest.fixture
def client(tmp_path, wav) -> TestClient:
    c = TestClient(create_app(data_dir=tmp_path))
    assert upload(c, wav).status_code == 200
    return c


def _meta(client) -> dict:
    return sources.list_sources(client.app.state.svc.data_dir)[0]


def _mix(client) -> np.ndarray:
    return sources.read(client.app.state.svc.data_dir, _meta(client))


def _post(client, path: str, **data):
    return client.post(path, data={"source": source_id(client), **data}, headers=LAB)


def test_separation_writes_four_stems_that_sum_to_the_mix(
    client, monkeypatch, tmp_path
):
    sid = separate(client, monkeypatch)
    meta = _meta(client)
    assert meta["stems"]["model"] == stems.HTDEMUCS.id
    d = tmp_path / "sources" / sid / f"stems-{stems.HTDEMUCS.id}"
    assert sorted(p.name for p in d.glob("*.flac")) == [
        "bass.flac",
        "drums.flac",
        "other.flac",
        "vocals.flac",
    ]
    data_dir = client.app.state.svc.data_dir
    mix = sources.read(data_dir, meta)
    total = sources.read_sum(data_dir, meta, list(sources.STEM_NAMES))
    assert total.shape == mix.shape
    assert np.allclose(total, mix - mix.mean(), atol=2e-3)  # less its DC
    assert client.app.state.svc.lab.last == {
        "source": sid,
        "error": "",
        "at": client.app.state.svc.lab.last["at"],
    }


def test_the_page_grows_a_lane_per_stem_and_the_region_plays_the_picked_ones(
    client, monkeypatch
):
    assert "stem.vocals" not in client.get("/view/lab").text
    sid = separate(client, monkeypatch)
    page = client.get("/view/lab").text
    for name in sources.STEM_NAMES:
        assert f'name="stem.{name}"' in page
    assert "Stems are separated" in page and "Separate into stems" not in page
    # each box posts the region form itself (a form-level `change from:find`
    # trigger never fired in a real browser)
    assert page.count('lab-auto" hx-post="/lab/region"') == 6
    _post(client, "/lab/region", **{"stems": "1", "stem.bass": "on"})
    meta = _meta(client)
    assert meta["picked"] == ["bass"] and lab.what(meta) == "bass"
    url = lab.source_context(client.app.state.svc.data_dir, meta, 120)["audio_url"]
    assert url.endswith("stems=bass")
    bass, _sr = read_pcm16(client.get(url).content)
    spectrum = np.abs(np.fft.rfft(bass[:, 0]))
    freqs = np.fft.rfftfreq(len(bass), 1 / SR)
    assert spectrum[freqs > 400].max() < 0.02 * spectrum.max()  # the low band only
    # every stem but one: the "instrumental" of the fake's bands
    three = client.get(
        f"/lab/{sid}/region.wav?start=0&length=1&stems=drums,bass,other,nonsense"
    )
    assert three.status_code == 200


def test_a_drag_on_a_lane_selects_on_that_lane(client, monkeypatch):
    """A drag on a stem's lane is the region
    on THAT stem — it alone; Shift held adds it to the ticked ones; on the
    mix lane, the recording; on the ruler (no lane), what it was."""
    separate(client, monkeypatch)
    drag = {"drag_a": "0.1", "drag_b": "1.9"}
    _post(client, "/lab/region", **drag, lane="vocals")
    assert _meta(client)["picked"] == ["vocals"]
    r = _post(client, "/lab/region", **drag, lane="drums", add="1")
    assert _meta(client)["picked"] == ["drums", "vocals"]  # in the set's order
    assert 'class="lab-lane lane-drums picked feeds"' in r.text
    _post(client, "/lab/region", **drag)  # the ruler: what plays stays
    assert _meta(client)["picked"] == ["drums", "vocals"]
    _post(client, "/lab/region", **drag, lane="mix")
    assert _meta(client)["picked"] == []
    for junk in ("nonsense", "../x", ""):
        _post(client, "/lab/region", **drag, lane=junk)
        assert _meta(client)["picked"] == []
    # the box ticks still win where no lane is named: the form's stems
    _post(client, "/lab/region", stems="1", **{"stem.bass": "on"})
    assert _meta(client)["picked"] == ["bass"]
    assert lab.pick_name(_meta(client)).startswith("bass ")


def test_a_sent_region_is_the_picked_stems_and_says_so(client, monkeypatch):
    separate(client, monkeypatch)
    _post(client, "/lab/region", stems="1", **{"stem.drums": "on", "stem.other": "on"})
    _post(client, "/lab/region", mode="bars", bar="1", bars="1")
    r = _post(client, "/lab/send", target="clip")
    kept = r.headers["X-Clip-Name"]
    assert kept == "drums-other bar 1"  # what it plays and where (lab.pick_name)
    data_dir = client.app.state.svc.data_dir
    audio, _sr = load_clip(data_dir, kept)
    meta = _meta(client)
    want = lab.region_audio(data_dir, meta)
    assert np.allclose(audio[: len(want)], want, atol=1e-3)
    assert not np.allclose(want, sources.read(data_dir, meta)[: len(want)], atol=1e-2)


def test_the_six_stem_model_draws_six_lanes_and_keeps_the_four_stem_set(
    client, monkeypatch, tmp_path
):
    """Guitar and piano are two more lanes; the song keeps both stem sets,
    and going back to the 4-stem one runs nothing."""
    six = stems.HTDEMUCS_6S
    sid = separate(client, monkeypatch)  # the 4-stem set first
    bank = fake_separation(client, monkeypatch, SixBands())
    r = _post(client, "/lab/separate", model=six.id)
    assert r.status_code == 200
    client.app.state.svc.lab.join()
    meta = _meta(client)
    assert meta["stems"]["model"] == six.id and bank.calls > 0
    assert sources.stem_names(six.id) == six.sources and len(six.sources) == 6
    folder = tmp_path / "sources" / sid
    assert sorted(
        p.name for p in (folder / f"stems-{six.id}").glob("*.flac")
    ) == sorted(f"{s}.flac" for s in six.sources)
    assert (folder / f"stems-{stems.HTDEMUCS.id}" / "vocals.flac").is_file()
    page = client.get("/view/lab").text
    for name in six.sources:
        assert f'name="stem.{name}"' in page
    assert page.count('class="lab-lane ') == 1 + 6
    # the six sum back to the mix, and the two new ones are picked like any
    data_dir = client.app.state.svc.data_dir
    total, mix = sources.read_sum(data_dir, meta, list(six.sources)), _mix(client)
    assert np.allclose(total, mix - mix.mean(), atol=2e-3)  # less its DC
    _post(client, "/lab/region", **{"stems": "1", "stem.guitar": "on"})
    assert _meta(client)["picked"] == ["guitar"]
    wav = client.get(f"/lab/{sid}/region.wav?start=0&length=1&stems=guitar,piano")
    assert wav.status_code == 200
    # back to four stems: the folder is still there, the model is not run
    calls = bank.calls
    assert _post(client, "/lab/separate", model=stems.HTDEMUCS.id).status_code == 200
    meta = _meta(client)
    assert meta["stems"]["model"] == stems.HTDEMUCS.id and bank.calls == calls
    assert meta["picked"] == []  # a stem the set does not hold is not picked
    assert "stem.guitar" not in client.get("/view/lab").text
    assert _post(client, "/lab/separate", model="nope").status_code == 422


def test_a_separator_with_the_wrong_stems_for_its_model_is_refused(client, monkeypatch):
    fake_separation(client, monkeypatch)  # four stems, asked for six
    _post(client, "/lab/separate", model=stems.HTDEMUCS_6S.id)
    client.app.state.svc.lab.join()
    assert _meta(client)["stems"]["model"] == ""
    assert "wrong stems" in client.app.state.svc.lab.last["error"]


def test_both_models_are_pinned_to_a_commit_and_a_hash():
    assert set(stems.MODELS) == {stems.HTDEMUCS.id, stems.HTDEMUCS_6S.id}
    for m in stems.MODELS.values():
        assert m.url.startswith("https://huggingface.co/") and "/resolve/" in m.url
        commit = m.url.split("/resolve/")[1].split("/")[0]
        assert len(commit) == 40 and int(commit, 16) >= 0  # not "main"
        assert len(m.sha256) == 64 and m.size < stems.MAX_MODEL_BYTES
        assert m.sources[:4] == stems.SOURCES and m.label and m.id in m.filename
    assert sources.ALL_STEMS == stems.HTDEMUCS_6S.sources
    assert sources.stem_names("from-another-build") == stems.SOURCES


def test_stems_run_once_a_second_separate_reuses_them(client, monkeypatch):
    bank = fake_separation(client, monkeypatch)
    for _ in range(2):
        assert _post(client, "/lab/separate").status_code == 200
        client.app.state.svc.lab.join()
    first = bank.calls
    assert first > 0
    assert _post(client, "/lab/separate").status_code == 200
    client.app.state.svc.lab.join()
    assert bank.calls == first


def test_a_second_job_is_told_busy_and_cancel_stops_the_first(client, monkeypatch):
    jobs = client.app.state.svc.lab
    bank = fake_separation(client, monkeypatch)
    gate, entered = threading.Event(), threading.Event()
    real = bank.run

    def slow(chunk):
        entered.set()
        assert gate.wait(30)
        return real(chunk)

    monkeypatch.setattr(bank, "run", slow)
    assert _post(client, "/lab/separate").status_code == 200
    assert entered.wait(30)
    again = _post(client, "/lab/separate")
    assert again.status_code == 422 and "one at a time" in again.text
    busy = client.get("/view/lab").text
    assert 'hx-trigger="every 1s"' in busy and "<progress" in busy
    refused = _post(client, "/lab/delete")
    assert refused.status_code == 422 and "cancel it first" in refused.text
    assert _post(client, "/lab/cancel").status_code == 200
    gate.set()
    jobs.join()
    assert jobs.state() is None and jobs.last["error"] == "Cancelled."
    assert _meta(client)["stems"]["model"] == ""
    assert not list(client.app.state.svc.data_dir.glob("sources/*/stems-*"))


def test_the_status_line_brings_the_panel_back_when_the_job_ends(client, monkeypatch):
    sid = separate(client, monkeypatch)
    idle = client.get(f"/fragment/lab/job?source={sid}", headers=LAB).text
    assert 'id="lab-job"' in idle and "hx-trigger" not in idle
    assert 'id="lab-panel"' not in idle
    ended = client.get(f"/fragment/lab/job?watch=1&source={sid}", headers=LAB).text
    assert 'id="lab-panel"' in ended and "hx-swap-oob" in ended
    assert 'name="stem.vocals"' in ended


def test_progress_is_published_per_segment_and_ends_done(client, monkeypatch):
    svc = client.app.state.svc
    heard = []
    monkeypatch.setattr(
        svc.events, "publish", lambda kind, data=None, **kw: heard.append((kind, data))
    )
    monkeypatch.setattr(type(svc.events), "active", property(lambda self: True))
    separate(client, monkeypatch)
    kinds = [k for k, _ in heard]
    assert kinds[-1] == "done" and "failed" not in kinds
    steps = [d for k, d in heard if k == "progress"]
    assert steps and steps[-1]["label"].startswith("Separating")
    assert max(d["done"] for d in steps) <= steps[-1]["total"]


def test_a_failing_separator_is_a_message_and_leaves_no_stems(client, monkeypatch):
    jobs = client.app.state.svc.lab
    fake_separation(client, monkeypatch)

    def broken():
        raise stems.StemsError("The separation model failed to load: bad graph")

    monkeypatch.setattr(jobs, "make_separator", broken)
    _post(client, "/lab/separate")
    jobs.join()
    assert "failed to load" in jobs.last["error"]
    assert "failed to load" in client.get("/view/lab").text
    assert _meta(client)["stems"]["model"] == ""

    def crashes():
        raise RuntimeError("anything at all")

    monkeypatch.setattr(jobs, "make_separator", crashes)
    _post(client, "/lab/separate")
    jobs.join()
    assert jobs.last["error"].startswith("Separation failed") and jobs.state() is None


def test_the_model_is_fetched_first_when_it_is_missing(client, monkeypatch):
    jobs = client.app.state.svc.lab
    bank = fake_separation(client, monkeypatch)
    have = {"model": False}
    monkeypatch.setattr(
        jobs,
        "availability",
        lambda: {"runtime": True, "megabytes": 316, "reason": "", **have},
    )
    order = []

    def fetch(progress, cancel):
        order.append("download")
        progress(2_000_000, 316_000_000)
        have["model"] = True

    monkeypatch.setattr(jobs, "fetch_model", fetch)
    monkeypatch.setattr(jobs, "make_separator", lambda: order.append("load") or bank)
    page = client.get("/view/lab").text
    assert "316 MB" in page and "hx-confirm" in page  # asks before downloading
    _post(client, "/lab/separate")
    jobs.join()
    assert order == ["download", "load"] and jobs.last["error"] == ""


def test_without_the_runtime_the_button_explains_and_the_lab_still_works(
    client, monkeypatch
):
    def missing():
        raise stems.StemsError(
            "Stem separation needs the onnxruntime package, which is not installed."
        )

    monkeypatch.setattr(stems, "runtime", missing)
    page = client.get("/view/lab").text
    assert "needs the onnxruntime package" in page
    assert page.count("Separate into stems</button>") == 1 and "disabled>Sep" in page
    r = _post(client, "/lab/separate")
    assert r.status_code == 422 and "onnxruntime" in r.text
    assert _post(client, "/lab/send", target="clip").status_code == 200  # phase 1


def test_a_half_written_stem_set_is_swept_not_trusted(client, monkeypatch, tmp_path):
    sid = source_id(client)
    d = tmp_path / "sources" / sid
    half = d / f"stems-{stems.HTDEMUCS.id}.tmp-deadbeef"
    half.mkdir()
    (half / "drums.flac").write_bytes(b"half")
    (d / "mix.flac.abc.tmp").write_bytes(b"junk")
    # a "finished" folder missing a stem is not a stem set either
    part = d / f"stems-{stems.HTDEMUCS.id}"
    part.mkdir()
    (part / "drums.flac").write_bytes(b"x")
    sources.update_meta(
        tmp_path, sid, lambda m: m.update(stems={"model": stems.HTDEMUCS.id})
    )
    assert _meta(client)["stems"]["model"] == ""
    restarted = TestClient(create_app(data_dir=tmp_path))  # a new studio sweeps
    assert not half.exists() and not (d / "mix.flac.abc.tmp").exists()
    assert "Separate into stems" in restarted.get("/view/lab").text
    separate(restarted, monkeypatch)  # and a real run replaces the partial folder
    assert _meta(restarted)["stems"]["model"] == stems.HTDEMUCS.id


def test_the_key_is_read_again_without_the_drums(client, monkeypatch):
    """... and kept when it is the surer reading. (The fake separator's
    "drums" band holds half the chords, so here the mix's reading is made
    unsure by hand; `test_a_less_sure_re_read_is_not_stored` is the other
    way round.)"""
    sid = source_id(client)
    sources.update_meta(
        client.app.state.svc.data_dir,
        sid,
        lambda m: m["analysis"].update(key="A minor", key_confidence=0.01),
    )
    separate(client, monkeypatch)
    an = _meta(client)["analysis"]
    assert an["key_from"] == "stems" and an["key"] and an["key_confidence"] > 0.01
    assert "without the drums" in client.get("/view/lab").text


def test_the_bass_stem_settles_the_key_against_its_relative(tmp_path, monkeypatch):
    """Chords that are as much F major as D minor, over a bass on D: the
    stems' reading says D minor because the bass line does, and it wins
    over a mix reading that said the relative — however sure that was."""
    pads, bass = pair_song("D D D D")
    c = TestClient(create_app(data_dir=tmp_path))
    assert upload(c, wav_bytes(pads + bass * 0.1)).status_code == 200
    sid = source_id(c)
    stale = {"key": "F major", "key_confidence": 0.99, "key_margin": 0.08}
    sources.update_meta(tmp_path, sid, lambda m: m["analysis"].update(stale))
    separate(c, monkeypatch, bank=BassSplit())
    an = sources.read_meta(tmp_path, sid)["analysis"]
    assert an["key"] == "D minor" and an["key_tonic"] == "bass", an
    assert an["key_margin"] > 0.1 and an["key_from"] == "stems"
    page = c.get("/view/lab").text
    assert "the bass line picked D minor over F major" in page


def test_read_again_asks_the_stems_and_a_song_without_stems_is_left_alone(
    client, monkeypatch
):
    """A song separated before the bass had a say keeps its old reading
    until "read again" (the phase-2 F major on a D minor song)."""
    assert "read again" not in client.get("/view/lab").text  # no stems yet
    before = _meta(client)["analysis"]
    assert _post(client, "/lab/key", reread="1").status_code == 200
    assert _meta(client)["analysis"] == before
    separate(client, monkeypatch)
    sid = source_id(client)
    stale = {"key": "C major", "key_confidence": 0.0, "key_from": "mix"}
    sources.update_meta(
        client.app.state.svc.data_dir, sid, lambda m: m["analysis"].update(stale)
    )
    r = _post(client, "/lab/key", reread="1")
    assert r.status_code == 200 and "read again" in r.text
    an = _meta(client)["analysis"]
    assert an["key_from"] == "stems" and an["key"] != "C major", an
    nobody = {"source": "0" * 16, "reread": "1"}
    assert client.post("/lab/key", data=nobody, headers=LAB).status_code == 422


def _reading(key: str, confidence: float, margin: float = 0.0) -> dict:
    return {"key": key, "key_confidence": confidence, "key_margin": margin}


def test_a_re_read_never_replaces_a_key_with_a_less_confident_one():
    """The regression this guards: the drumless re-read turned a right
    D minor into F major. Newer is no argument; surer is."""
    have = _reading("D minor", 0.4)
    assert not lab.better_key(have, _reading("G major", 0.39))
    assert not lab.better_key(have, _reading("", 0.0))
    assert lab.better_key(have, _reading("G major", 0.41))
    assert lab.better_key(have, _reading("D minor", 0.4))
    assert lab.better_key(_reading("", 0.0), _reading("C major", 0.01))
    # a relative pair is the tonic's question, which confidence does not
    # measure: the reading that leads its relative by more wins, both ways
    major, minor = _reading("F major", 0.9, 0.05), _reading("D minor", 0.1, 0.11)
    assert lab.better_key(major, minor) and not lab.better_key(minor, major)
    assert not lab.better_key(have, _reading("F major", 0.39))  # no margin: no case
    assert lab.relatives("A minor", "C major") and lab.relatives("F major", "D minor")
    assert not lab.relatives("C major", "C minor") and not lab.relatives("x", "C major")


def test_a_less_sure_re_read_is_not_stored(client, monkeypatch):
    sid = source_id(client)
    sources.update_meta(
        client.app.state.svc.data_dir,
        sid,
        lambda m: m["analysis"].update(key="E major", key_confidence=1.0),
    )
    separate(client, monkeypatch)
    an = _meta(client)["analysis"]
    assert an["key"] == "E major" and an["key_from"] == "mix"


def test_a_project_bundle_carries_what_the_session_uses_never_the_sources(
    client, monkeypatch
):
    separate(client, monkeypatch)
    _post(client, "/lab/region", mode="bars", bar="1", bars="1")
    assert _post(client, "/lab/send", target="sampler", name="cut").status_code == 200
    bundle = zipfile.ZipFile(io.BytesIO(client.get("/project.zip").content))
    names = bundle.namelist()
    sample = client.app.state.svc.state.patch.sampler_sample
    assert f"samples/{sample}.wav" in names
    assert not [n for n in names if "sources" in n or n.endswith(".flac")]
    assert b"sources" not in bundle.read("project.json")


# --- a level per stem: the region's mix ------------------------------------------


def _region_wav(client) -> np.ndarray:
    svc = client.app.state.svc
    url = lab.source_context(svc.data_dir, _meta(client), 120)["audio_url"]
    r = client.get(url)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return read_pcm16(r.content)[0]


def _weighted(client, levels: dict[str, float]) -> np.ndarray:
    """The manual sum: each stem's own file over the region, times its gain."""
    data_dir, meta = client.app.state.svc.data_dir, _meta(client)
    start, length = lab.span(meta)
    first, frames = int(round(start * SR)), int(round(length * SR))
    return sum(
        sources.read(data_dir, meta, s, first, frames).astype(np.float64)
        * 10 ** (db / 20)
        for s, db in levels.items()
    )


def test_each_stem_lane_has_a_level_slider_and_a_solo(client, monkeypatch):
    assert 'name="level.' not in client.get("/view/lab").text  # no stems, no levels
    separate(client, monkeypatch)
    meta = _meta(client)
    # a meta written before levels existed: every stem at 0 dB
    assert meta["levels"] == {} and set(sources.levels(meta).values()) == {0.0}
    page = client.get("/view/lab").text
    for name in sources.STEM_NAMES:
        assert f'id="lab-level-{name}" name="level.{name}"' in page
        assert f'id="lab-solo-{name}"' in page
    lo, hi = (int(v) for v in limits.LAB_LEVEL_DB_RANGE)
    assert page.count(f'min="{lo}" max="{hi}" step="0.5" value="0"') == 4
    # a slider's post morphs the body, so the slider keeps the keyboard
    assert page.count('hx-swap="outerMorph" hx-trigger="change"') == 4
    # the one-stem makes (voice, vocal chops, drum pattern, kit) say so
    assert page.count("the lane levels are not used") == 4


def test_the_audition_and_a_sent_clip_are_the_weighted_sum(client, monkeypatch):
    sid = separate(client, monkeypatch)
    _post(client, "/lab/region", mode="bars", bar="1", bars="1")
    form = {"stems": "1", "stem.bass": "on", "stem.drums": "on", "stem.other": "on"}
    r = _post(client, "/lab/region", **form, **{"level.bass": "-6", "level.other": "3"})
    assert r.status_code == 200
    assert 'name="level.bass"' in r.text and "−6 dB" in r.text and "+3 dB" in r.text
    data_dir, meta = client.app.state.svc.data_dir, _meta(client)
    model = meta["stems"]["model"]
    # stored with the source, per stem set, 0 dB left out
    assert meta["levels"] == {model: {"bass": -6.0, "other": 3.0}}
    url = lab.source_context(data_dir, meta, 120)["audio_url"]
    assert url.endswith("&levels=0,-6,3&stems=drums,bass,other")
    want = _weighted(client, {"drums": 0.0, "bass": -6.0, "other": 3.0})
    assert np.max(np.abs(want)) < 1.0  # nothing was scaled: the sums compare
    got = _region_wav(client)
    assert np.allclose(got, want[: len(got)], atol=2e-4)  # 16-bit steps
    # not the unity sum
    unity = _weighted(client, {"drums": 0.0, "bass": 0.0, "other": 0.0})
    assert np.max(np.abs(want - unity)) > 0.01
    # a clip sent from the region is the same mix, and its meta says so
    kept = _post(client, "/lab/send", target="clip").headers["X-Clip-Name"]
    audio, _sr = load_clip(data_dir, kept)
    assert np.allclose(audio[: len(want)], want[: len(audio)], atol=2e-4)
    info = next(c for c in list_clips(data_dir) if c["name"] == kept)["lab"]
    assert info["levels"] == {"bass": -6.0, "other": 3.0}
    assert info["stems"] == ["drums", "bass", "other"]
    # the sampler sound and the song take are made from the same audio
    for audio in (
        lab.sampler_audio(data_dir, meta)[0],
        lab.clip_audio(data_dir, meta, 120.0, False)[0],
        labmake.song_take(data_dir, meta, lab.grid(meta)["bpm"])[0],
    ):
        n = min(len(audio), len(want))  # a take is padded to whole bars
        assert n > SR and np.allclose(audio[:n], want[:n], atol=1e-6)
    # levels in the query are the audition's own: another value, another mix
    quiet = client.get(f"/lab/{sid}/region.wav?start=0&length=1&levels=-24&stems=bass")
    plain = client.get(f"/lab/{sid}/region.wav?start=0&length=1&stems=bass")
    a, b = read_pcm16(quiet.content)[0], read_pcm16(plain.content)[0]
    assert np.allclose(a, b * 10 ** (-24 / 20), atol=2e-4)


def test_with_every_level_at_zero_the_sum_is_the_plain_one(client, monkeypatch):
    separate(client, monkeypatch)
    data_dir, meta = client.app.state.svc.data_dir, _meta(client)
    names = list(sources.STEM_NAMES)
    plain = sources.read_sum(data_dir, meta, names)
    zeros = sources.read_sum(data_dir, meta, names, levels=dict.fromkeys(names, 0.0))
    assert np.array_equal(plain, zeros)
    # the mix has no level: with nothing picked a level changes nothing
    before = lab.region_audio(data_dir, meta)
    _post(client, "/lab/region", stems="1", **{"level.vocals": "-12"})
    meta = _meta(client)
    assert meta["picked"] == [] and sources.levels(meta)["vocals"] == -12.0
    assert np.array_equal(lab.region_audio(data_dir, meta), before)


def test_hostile_levels_are_clamped_and_never_a_500(client, monkeypatch, tmp_path):
    sid = separate(client, monkeypatch)
    lo, hi = limits.LAB_LEVEL_DB_RANGE
    for raw, want in [
        ("1e99", 0.0),  # not a sane number at all
        ("999", hi),
        ("-999", lo),
        ("nan", 0.0),
        ("inf", 0.0),
        ("-inf", 0.0),
        ("loud", 0.0),
        ("", 0.0),
        ("-0", 0.0),
        ("2.26", 2.3),
        ("9" * 400, 0.0),
    ]:
        r = _post(client, "/lab/region", stems="1", **{"level.bass": raw})
        assert r.status_code == 200, raw
        assert sources.levels(_meta(client))["bass"] == want, raw
    # a stem the set does not hold, and a level without the stems switch
    _post(client, "/lab/region", stems="1", **{"level.guitar": "3", "level.x": "3"})
    _post(client, "/lab/region", **{"level.drums": "3"})
    assert _meta(client)["levels"] == {}
    # meta.json is a file anyone can edit
    path = tmp_path / "sources" / sid / "meta.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    model = raw["stems"]["model"]
    for bad in (
        "loud",
        [1, 2],
        {model: "x"},
        {model: {"bass": "x", "drums": True, "other": None, "vocals": [3]}},
        {"nope": {"bass": 3}, model: {"guitar": 3, "bass": float("nan")}},
    ):
        path.write_text(json.dumps({**raw, "levels": bad}), encoding="utf-8")
        assert _meta(client)["levels"] == {}, bad
        assert client.get("/view/lab").status_code == 200
    path.write_text(
        json.dumps({**raw, "levels": {model: {"bass": 1e9, "vocals": -1e9}}}),
        encoding="utf-8",
    )
    assert _meta(client)["levels"] == {model: {"bass": hi, "vocals": lo}}
    # the audition's query: bounded, never trusted
    for q in (
        "levels=nan,inf,-inf,x&stems=drums,bass,other,vocals",
        "levels=1e99,-1e99&stems=bass,drums",
        "levels=" + "9," * 500 + "&stems=" + "bass," * 500,
        "levels=3,3,3&stems=nonsense,../x,bass",
        "levels=&stems=bass",
        "levels=3",
    ):
        r = client.get(f"/lab/{sid}/region.wav?start=0&length=1&{q}")
        assert r.status_code == 200, q
        assert len(read_pcm16(r.content)[0]) == SR


def test_a_boost_past_full_scale_is_scaled_down_not_clipped(client, monkeypatch):
    separate(client, monkeypatch)
    data_dir, meta = client.app.state.svc.data_dir, _meta(client)
    names = list(sources.STEM_NAMES)
    plain = sources.read_sum(data_dir, meta, names)
    hot = sources.read_sum(data_dir, meta, names, levels=dict.fromkeys(names, 6.0))
    assert np.max(np.abs(plain)) * 10 ** (6 / 20) > 1.0  # it would have clipped
    assert abs(np.max(np.abs(hot)) - 1.0) < 1e-6
    scale = np.max(np.abs(plain)) / np.max(np.abs(hot))
    assert np.allclose(hot * scale, plain, atol=1e-5)  # the same waveform


def test_solo_plays_one_stem_alone_and_again_gives_every_stem_back(client, monkeypatch):
    separate(client, monkeypatch)
    form = {"stems": "1", "stem.drums": "on", "stem.bass": "on"}
    r = _post(client, "/lab/region", **form, solo="vocals")
    assert _meta(client)["picked"] == ["vocals"]
    pressed = re.findall(r'aria-pressed="true"\s+aria-label="solo (\w+)"', r.text)
    assert 'id="lab-solo-vocals"' in r.text and pressed == ["vocals"]
    _post(client, "/lab/region", **{"stems": "1", "stem.vocals": "on"}, solo="vocals")
    assert _meta(client)["picked"] == list(sources.STEM_NAMES)
    _post(client, "/lab/region", **form, solo="nonsense")
    assert _meta(client)["picked"] == ["drums", "bass"]


def test_the_one_stem_actions_ignore_the_levels(client, monkeypatch):
    """A vocal instrument, vocal chops, a drum pattern and a kit each read
    ONE stem (`labmake.stem_region`): that stem's level in the region's mix
    is not theirs."""
    separate(client, monkeypatch)
    _post(client, "/lab/region", mode="bars", bar="1", bars="1")
    data_dir = client.app.state.svc.data_dir
    before = labmake.stem_region(data_dir, _meta(client), "drums")[0]
    form = {"stems": "1", "stem.drums": "on", "level.drums": "-24", "level.bass": "6"}
    _post(client, "/lab/region", **form)
    meta = _meta(client)
    assert sources.levels(meta)["drums"] == -24.0
    after, where, _ = labmake.stem_region(data_dir, meta, "drums")
    assert where == "drums" and np.array_equal(before, after)
    assert not np.allclose(lab.region_audio(data_dir, meta), after)  # the mix is


def test_each_stem_set_keeps_its_own_levels(client, monkeypatch):
    six = stems.HTDEMUCS_6S
    separate(client, monkeypatch)
    _post(client, "/lab/region", stems="1", **{"level.vocals": "-9"})
    fake_separation(client, monkeypatch, SixBands())
    _post(client, "/lab/separate", model=six.id)
    client.app.state.svc.lab.join()
    assert set(sources.levels(_meta(client)).values()) == {0.0}  # the new set's
    _post(client, "/lab/region", stems="1", **{"level.piano": "4.5"})
    assert _post(client, "/lab/separate", model=stems.HTDEMUCS.id).status_code == 200
    meta = _meta(client)
    assert sources.levels(meta)["vocals"] == -9.0
    assert "piano" not in sources.levels(meta)
    assert meta["levels"] == {
        stems.HTDEMUCS.id: {"vocals": -9.0},
        six.id: {"piano": 4.5},
    }


def test_levels_touch_no_session_state_and_no_render_key(client, monkeypatch):
    svc = client.app.state.svc
    before = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    separate(client, monkeypatch)
    form = {"stems": "1", "stem.bass": "on", "level.bass": "-6"}
    _post(client, "/lab/region", **form, solo="drums")
    assert sources.levels(_meta(client))["bass"] == -6.0
    after = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    assert before == after
