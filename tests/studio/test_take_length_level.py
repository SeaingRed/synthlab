"""A take's loop where its take was: at the keys' level (`keylevel.py`) and,
when the take is not whole bars, the question after it — keep, trim or fit
the tempo (`routes/takes.py` `/loop/length`).

The level: a loop render is normalised and the key bus is not, so a take's
loop played ~9 dB over its take (measured in Chromium:
`tests/browser/test_take_level.py`). Nothing renders differently — every
key holds — the players apply a gain the server names: `preview-ready`'s
`gain` on Perform while a saved loop of notes is open, `X-Preview-Gain` on
a Loops row's render at another tempo, and a loop's bounced audio is kept
at that level.
"""

from __future__ import annotations

import io
import json
import math
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import keylevel, loops
from synthlab.studio.server import create_app
from synthlab.studio.state.model import Arrangement


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


PERFORM = _hx("perform")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _wav(seconds: float = 0.5, sr: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x10" * int(seconds * sr) * 2)
    return buf.getvalue()


def _rms_db(raw: bytes) -> float:
    with wave.open(io.BytesIO(raw)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32768.0
    return 20 * math.log10(float(np.sqrt(np.mean(pcm**2))))


# a riff of 2.24 bars at 100 bpm, nothing playing under it: the owner's case
RIFF = [
    [0.1, 0.5, 60, 0.8],
    [2.0, 0.5, 64, 0.8],
    [4.1, 0.5, 67, 0.8],
    [8.5, 0.4, 72, 0.8],
]


def _take(client, notes=RIFF, bars="2.24", bpm="100", name="keys-1"):
    data = {"name": name, "bpm": bpm, "bars": bars, "notes": json.dumps(notes)}
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post("/take/keys", data=data, files=files, headers=_hx("editor"))


def _trigger(resp) -> dict:
    return json.loads(resp.headers.get("HX-Trigger") or "{}")


# --- the key bus's gain ----------------------------------------------------------


def test_the_key_bus_gain_for_single_notes_chords_and_their_tails():
    one = keylevel.KEY_HEADROOM * keylevel.KEY_MAKEUP
    assert keylevel.KEY_MAKEUP == pytest.approx(10 ** (2.28 / 20))
    # a line of single notes: one voice's gain, whatever the gaps
    line = [(0, 1, 1.2), (2, 3, 3.2), (4, 4.5, 4.7)]
    assert keylevel.keys_gain(line) == pytest.approx(one)
    # a held three-note chord with no tail: the bus's gain for three
    chord = [(0, 2, 2), (0, 2, 2), (0, 2, 2)]
    assert keylevel.keys_gain(chord) == pytest.approx(one / 3**0.25)
    # its tail rings at one voice's gain (the keys are up): between the two
    tailed = [(0, 2, 3), (0, 2, 3), (0, 2, 3)]
    assert one / 3**0.25 < keylevel.keys_gain(tailed) < one
    assert keylevel.keys_gain([]) is None


# --- the players' gain: Perform's transport, a row's render, a bounce ------------


def test_an_open_loop_plays_at_the_keys_level_on_perform_and_only_there(client):
    _take(client, bars="2")
    s = _st(client)
    r = client.post("/loop/edit", data={"name": "keys-1"}, headers=PERFORM)
    detail = _trigger(r)["preview-ready"]
    gain = detail["gain"]
    assert gain < -3  # a normalised render, turned down to the keys
    # exactly the key bus's gain for its notes less the normalisation's
    svc = client.app.state.svc
    keys = keylevel.keys_gain(keylevel.held_spans(svc.render_inputs("perform")))
    norm = svc.render("perform").norm_gain_db
    assert gain == pytest.approx(round(20 * math.log10(keys) - norm, 2))
    # Done: the working loop plays at the mix's level again
    r = client.post("/loop/edit/done", headers=PERFORM)
    r = client.post("/fragment/preview", data={"view": "perform"}, headers=PERFORM)
    assert "gain" not in _trigger(r)["preview-ready"]
    # Synths is never turned
    client.post("/loop/edit", data={"name": "keys-1"}, headers=PERFORM)
    r = client.post("/fragment/preview", data={"view": "editor"}, headers=_hx("editor"))
    assert "gain" not in _trigger(r)["preview-ready"]
    assert s.loop_edit_of()["name"] == "keys-1"


def test_a_rows_render_at_another_tempo_names_its_gain(client):
    _take(client, bars="2")
    r = client.post(
        "/loop/preview", data={"name": "keys-1", "bpm": "90"}, headers=PERFORM
    )
    url, gain = r.headers["X-Preview-Url"], float(r.headers["X-Preview-Gain"])
    assert url.startswith("/preview/") and gain < -3
    # at its own tempo: its take, at the level it was recorded — no gain
    r = client.post("/loop/preview", data={"name": "keys-1"}, headers=PERFORM)
    assert r.headers["X-Preview-Url"] == "/clip/keys-1.wav"
    assert "X-Preview-Gain" not in r.headers


def test_a_loop_with_no_audio_is_bounced_at_the_keys_level(client):
    s = _st(client)
    s.arrangement = Arrangement(bpm=110, cells=[["A2", 0], ["C3", 4]])
    client.post(
        "/loop/save", data={"lane": "1", "bars": "1", "loop": "riff"}, headers=PERFORM
    )
    other = client.post(
        "/loop/preview", data={"name": "riff", "bpm": "110.5"}, headers=PERFORM
    )
    rendered = _rms_db(client.get(other.headers["X-Preview-Url"]).content)
    gain = float(other.headers["X-Preview-Gain"])
    r = client.post("/loop/preview", data={"name": "riff"}, headers=PERFORM)
    take = loops.load(s.data_dir, "riff")["take"]
    assert r.headers["X-Preview-Url"] == f"/clip/{take}.wav"
    kept = _rms_db(client.get(r.headers["X-Preview-Url"]).content)
    # its own tempo's render, kept at the keys' level (110.5 bpm is ~0.5 %
    # off its tempo: the same notes, a hair faster)
    assert kept == pytest.approx(rendered + gain, abs=0.3)
