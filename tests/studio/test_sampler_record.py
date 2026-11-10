"""● Record sample in the sampler panel (`POST /fragment/sampler/record`,
static/samplerec.js): a mic take timed to the session's bpm becomes a clip
(`rec-YYYYmmdd-HHMMSS`, meta source "sampler-rec", bpm, bars) and then the
sampler sound, through the same load "Use as sampler" runs on a clip."""

from __future__ import annotations

import io
import json
import re
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.studio.clips import clip_dir
from synthlab.studio.server import create_app

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _wav(seconds=1.0, sr=44100, hz=220.0, width=2, level=0.5) -> bytes:
    n = int(sr * seconds)
    x = np.sin(2 * np.pi * hz * np.arange(n) / sr) * level
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(sr)
        if width == 2:
            w.writeframes((x * 32767).astype("<i2").tobytes())
        else:  # 24-bit: three bytes a sample
            ints = (x * 8388607).astype("<i4").tobytes()
            w.writeframes(b"".join(ints[i : i + 3] for i in range(0, len(ints), 4)))
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _record(client, data: bytes | None, bars="1"):
    files = {"file": ("blob", data, "audio/wav")} if data is not None else None
    return client.post(
        "/fragment/sampler/record", data={"bars": bars}, files=files, headers=HX
    )


def _clips(tmp_path) -> list[str]:
    d = clip_dir(tmp_path)
    return sorted(p.stem for p in d.glob("*.wav")) if d.exists() else []


def test_a_recording_becomes_a_clip_and_the_sampler(client, tmp_path):
    s = client.app.state.studio
    bpm = s.arrangement.bpm
    secs = 2 * 240 / bpm  # two bars, as the browser cuts them
    r = _record(client, _wav(seconds=secs), bars="2")
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "preview-ready" in trig
    assert "Recorded rec-" in trig["studio-toast"]["message"]
    # kept in the clip store under a timestamp, with its clock
    (name,) = _clips(tmp_path)
    assert re.fullmatch(r"rec-\d{8}-\d{6}", name)
    meta = json.loads((clip_dir(tmp_path) / f"{name}.meta.json").read_text("utf-8"))
    assert meta["source"] == "sampler-rec" and meta["bars"] == 2 and meta["bpm"] == bpm
    # the sampler holds it: a fresh sampler sound named after the clip
    p = s.patch
    assert p.osc1 == "sampler" and sampler.valid_id(p.sampler_sample)
    assert p.name == name
    assert sampler.seconds(p.sampler_sample) == pytest.approx(secs, abs=0.01)
    # its root came from the take's pitch (220 Hz is A3), as a clip's does
    assert p.sampler_root == 57
    # the page comes back whole, with the panel drawing the sample, and stays
    assert 'id="sampler-panel"' in r.text and "sampler-svg" in r.text
    assert "HX-Push-Url" not in r.headers
    # ▶ plays it
    assert np.abs(client.app.state.svc.render("editor").audio()).max() > 0.05


def test_a_recording_is_one_undo_step(client):
    s = client.app.state.studio
    assert _record(client, _wav()).status_code == 200
    assert s.patch.osc1 == "sampler"
    client.post("/fragment/undo", headers=HX)
    assert s.patch.osc1 == "saw" and not s.patch.sampler_sample


def test_two_recordings_in_one_second_do_not_overwrite(client, tmp_path):
    assert _record(client, _wav(hz=220)).status_code == 200
    assert _record(client, _wav(hz=330)).status_code == 200
    assert len(_clips(tmp_path)) == 2


@pytest.mark.parametrize(
    ("data", "bars", "said"),
    [
        (b"RIFF not a wav at all", "1", "read that recording"),
        (_wav(width=3), "1", "read that recording"),  # 24-bit: not the format we send
        (_wav(sr=4000), "1", "Unsupported recording (4000 Hz)"),
        (_wav(level=0.0), "1", "silent"),
        (None, "1", "No recording"),
        (_wav(), "3", "1, 2 or 4 bars"),
        (_wav(), "two", "1, 2 or 4 bars"),
    ],
    ids=["not-wav", "24-bit", "sample-rate", "silent", "no-file", "bars", "bars-text"],
)
def test_hostile_recordings_are_refused_with_a_toast(
    client, tmp_path, data, bars, said
):
    s = client.app.state.studio
    r = _record(client, data, bars=bars)
    assert r.status_code == 422 and said in r.text and 'class="toast' in r.text
    assert _clips(tmp_path) == []  # nothing written
    assert s.patch.osc1 == "saw"


def test_a_file_over_the_take_cap_is_a_toast(client, tmp_path, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_TAKE_UPLOAD_BYTES", 1000)
    r = _record(client, _wav())
    assert r.status_code == 422 and "too large" in r.text
    assert _clips(tmp_path) == []


def test_a_body_over_the_upload_cap_never_reaches_the_route(client, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_UPLOAD_BYTES", 1000)
    assert _record(client, _wav()).status_code == 413


def test_the_panel_offers_it_empty_and_loaded(client):
    """The record control sits beside Resample, in the empty state too:
    a red `.btn` with its command, the lengths as a `.seg` of buttons and
    the click a toggle — buttons only, so the #patch form posts nothing."""
    client.post("/fragment/patch", data={"osc1": "sampler"}, headers=HX)
    for _ in range(2):
        html = client.get("/view/editor", headers=HX).text
        group = 'aria-label="record a sample from the microphone"'
        panel = html.split('id="sampler-panel"')[1].split(group)[1].split("</div>")[0]
        assert 'id="smp-rec" class="btn btn-s btn-rec"' in panel
        assert 'data-cmd="Sampler: record a sample from the mic"' in panel
        assert re.findall(r'data-smp-bars="(\d)"', panel) == ["1", "2", "4"]
        assert 'id="smp-click"' in panel and 'id="smp-rec-status"' in panel
        assert "<input" not in panel and "<select" not in panel
        _record(client, _wav())
    # the count-in's overlay sits outside the form, and posts the take
    assert re.search(r'id="smp-countdown"[^>]*hx-encoding="multipart/form-data"', html)
    form_end = html.index("</form>", html.index('id="patch"'))
    assert html.index('id="smp-countdown"') > form_end
