"""Import SFZ… (`POST /fragment/sampler/sfz`, `synthlab/sfz.py`): a zip of an
.sfz and its samples becomes the edited sound — a sampler with a zone a
region — as one undo step, with a toast that lists what it did not read;
a hostile archive is a toast and changes nothing; the live keys play it
(or fall back to the WAV keys, said). The reader's own rules are
tests/test_sfz.py's."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.studio import limits, security
from synthlab.studio import samplerview as sv
from synthlab.studio.security import BodySizeLimit
from synthlab.studio.server import create_app
from tests.sfzkit import small_instrument, wav, zip_of

EDITOR = {
    "HX-Request": "true",
    "HX-Current-URL": "http://testserver/view/editor",
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _post(client, data: bytes, name: str = "inst.zip"):
    return client.post(
        "/fragment/sampler/sfz",
        files={"file": (name, data, "application/zip")},
        headers=EDITOR,
    )


def _toast(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def _state(client):
    return client.app.state.studio


def test_a_zip_becomes_the_sound_in_one_undo_step(client, tmp_path):
    s = _state(client)
    before = s.patch
    r = _post(client, small_instrument())
    assert r.status_code == 200
    zones = sv.zones_of(s.patch)
    assert len(zones) == 4 and s.patch.osc1 == "sampler" and s.patch.name == "inst"
    assert (s.patch.attack, s.patch.release) == (0.01, 0.4)
    for z in zones:  # every zone's audio is in the sample store, by content
        assert (tmp_path / "samples" / f"{z.sample}.wav").is_file()
    assert "Imported inst: 4 zones from 3 samples." in _toast(r)
    assert 'id="zone-map"' in r.text and r.text.count('class="zone-row') == 4
    # it plays: each key from its own zone
    assert abs(client.app.state.svc.render("editor").audio()).max() > 0.05
    client.post("/fragment/undo", headers=EDITOR)
    assert s.patch == before


def test_what_is_not_read_is_in_the_toast(client):
    data = zip_of(
        {
            "a.sfz": "<group> cutoff=900 ampeg_hold=0.2\n"
            "<region> sample=s.wav key=c4 pan=10\n"
            "<region> sample=s.wav key=d4 trigger=release",
            "s.wav": wav(220),
        }
    )
    msg = _toast(_post(client, data))
    assert "cutoff ×2" in msg and "ampeg_hold ×2" in msg and "pan" in msg
    assert "release / first / legato triggers" in msg


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"<region> sample=x.wav", "Send a .zip"),
        (zip_of({"s.wav": wav(220)}), "no .sfz"),
        (zip_of({"a.sfz": "<region> sample=s.wav", "../s.wav": b"x"}), "will not read"),
        (zip_of({"a.sfz": '#include "a.sfz"'}), "loops back"),
        (zip_of({"a.sfz": "<region> sample=gone.wav"}), "No region"),
    ],
)
def test_a_bad_archive_is_a_toast_and_nothing_changes(client, tmp_path, data, why):
    s = _state(client)
    before = s.patch
    r = _post(client, data)
    assert r.status_code == 422 and why in r.text
    assert s.patch == before
    assert (
        not list((tmp_path / "samples").glob("*"))
        if (tmp_path / "samples").exists()
        else True
    )


def test_no_file_is_a_toast(client):
    r = client.post("/fragment/sampler/sfz", data={"file": "x"}, headers=EDITOR)
    assert r.status_code == 422


def test_the_reader_limits_come_from_limits_py(client, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SFZ_MEMBERS", 2)
    r = _post(client, small_instrument())
    assert r.status_code == 422 and "more than 2 files" in r.text


def test_the_upload_is_capped_by_the_limits_module(client, monkeypatch):
    assert (
        BodySizeLimit._limit_for("/fragment/sampler/sfz") == limits.MAX_SFZ_UPLOAD_BYTES
    )
    big = small_instrument() + bytes(limits.MAX_SFZ_UPLOAD_BYTES)
    assert _post(client, big).status_code == 413  # the middleware
    monkeypatch.setattr(limits, "MAX_SFZ_UPLOAD_BYTES", 64)
    monkeypatch.setitem(
        security._PATH_UPLOAD_LIMITS, "/fragment/sampler/sfz", "MAX_UPLOAD_BYTES"
    )
    r = _post(client, small_instrument())  # under the body cap, over the part's
    assert r.status_code == 413 and "too large" in r.text


def test_the_imported_instrument_plays_on_the_live_keys(client):
    _post(client, small_instrument())
    s = _state(client)
    assert sampler.live_ok(s.patch)
    pj = client.get("/patch.json").json()
    assert pj["worklet_ok"], pj["worklet_blockers"]
    r = client.get(pj["sampler_live"])
    assert r.status_code == 200
    assert "worklet_ok" in pj and len(sampler.parse_zones(s.patch.sampler_zones)) == 4


def test_past_the_live_cap_the_keys_fall_back_to_the_wav_and_the_toast_says_so(
    client, monkeypatch
):
    monkeypatch.setattr(sampler, "LIVE_MAX_SAMPLES", 1000)
    r = _post(client, small_instrument())
    assert r.status_code == 200
    assert "rendered notes instead" in _toast(r)
    pj = client.get("/patch.json").json()
    assert not pj["worklet_ok"] and "sampler_zones" in " ".join(pj["worklet_blockers"])


def test_the_buttons_and_the_form_outside_the_patch_form(client):
    page = client.get("/view/editor").text
    head, _sep, rest = page.partition('<form id="patch"')
    body, sep, tail = rest.partition("</form>")
    assert sep and 'for="sfz-file"' in head  # the sound dock's, always there
    assert 'id="sfz-file"' not in body  # the input is not inside #patch
    assert 'hx-post="/fragment/sampler/sfz"' in tail
    assert 'hx-encoding="multipart/form-data"' in tail
    assert 'type="file" id="sfz-file"' in tail
    _post(client, small_instrument())
    page = client.get("/view/editor").text
    # the dock's, the panel's and the zone map's toolbar
    assert page.count('for="sfz-file"') == 3
