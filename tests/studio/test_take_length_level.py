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


# --- the take's length: keep, trim, fit -----------------------------------------


def test_a_take_off_the_bar_lines_asks_keep_trim_or_fit(client):
    r = _take(client)
    ask = _trigger(r)["song-ask"]
    labels = [c["label"] for c in ask["choices"]]
    assert labels == ["Keep 3 bars", "Trim to 2 bars", "Fit the tempo"]
    assert all(c["path"] == "/loop/length" for c in ask["choices"])
    assert [c["vals"]["how"] for c in ask["choices"]] == ["keep", "trim", "fit"]
    assert "89.286 bpm" in ask["choices"][2]["title"]  # 100 x 2 / 2.24
    assert "2.24 bars" in ask["message"]
    # a take of whole bars asks nothing
    r = _take(client, bars="2", name="keys-2")
    assert "song-ask" not in _trigger(r)


def test_keep_trim_and_fit_each_place_the_loop_from_its_take(client):
    _take(client)
    s = _st(client)
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["bars"] == 3 and loop["key"]["bpm"] == 100  # as taken
    post = {"name": "keys-1"}
    # fit: 89.286 bpm, two bars exactly, every note where it was in time
    r = client.post("/loop/length", data=post | {"how": "fit"}, headers=PERFORM)
    assert r.status_code == 200 and "89.286 bpm" in r.headers["HX-Trigger"]
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["bars"] == 2 and loop["key"]["bpm"] == pytest.approx(89.286)
    k = 2 / 2.24
    for got, was in zip(loop["played"], RIFF, strict=True):
        assert got[0] == pytest.approx(was[0] * k, abs=1e-5)
        assert got[1] == pytest.approx(was[1] * k, abs=1e-5)
        assert got[0] * 60 / 89.286 == pytest.approx(was[0] * 60 / 100, abs=1e-3)
    # trim: two bars at 100 bpm, the note past bar 2's line gone
    client.post("/loop/length", data=post | {"how": "trim"}, headers=PERFORM)
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["bars"] == 2 and loop["key"]["bpm"] == 100
    assert [n[2] for n in loop["played"]] == [60, 64, 67]
    # keep: back as taken
    client.post("/loop/length", data=post | {"how": "keep"}, headers=PERFORM)
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["lane"]["bars"] == 3 and loop["played"] == RIFF
    assert loop["key"]["bpm"] == 100 and loop["edited"] is False


def test_trim_cuts_a_note_held_over_the_line_at_it(client):
    held = [[0, 1, 60, 1], [7, 2, 62, 1]]  # the second held past bar 2's end
    _take(client, notes=held, bars="2.4")
    client.post("/loop/length", data={"name": "keys-1", "how": "trim"}, headers=PERFORM)
    loop = loops.load(_st(client).data_dir, "keys-1")
    assert loop["played"] == [[0, 1, 60, 1], [7, 1.0, 62, 1]]


def test_open_on_the_grid_it_follows_and_ctrl_z_puts_it_all_back(client):
    _take(client)
    s = _st(client)
    client.post("/loop/edit", data={"name": "keys-1"}, headers=PERFORM)
    assert (s.arrangement.bars, s.arrangement.bpm) == (3, 100)
    r = client.post(
        "/loop/length", data={"name": "keys-1", "how": "fit"}, headers=PERFORM
    )
    trig = _trigger(r)
    assert "preview-ready" in trig and "gain" in trig["preview-ready"]  # heard now
    assert s.arrangement.bars == 2 and s.arrangement.bpm == pytest.approx(89.286)
    client.post("/fragment/pattern/undo", headers=PERFORM)
    assert (s.arrangement.bars, s.arrangement.bpm) == (3, 100)
    loop = loops.load(s.data_dir, "keys-1")
    assert loop["played"] == RIFF and loop["key"]["bpm"] == 100
    assert loop["lane"]["bars"] == 3


def test_what_cannot_be_set_is_refused_in_words(client):
    s = _st(client)
    r = client.post(
        "/loop/length", data={"name": "keys-1", "how": "stretch"}, headers=PERFORM
    )
    assert r.status_code == 422 and "one of the three" in r.text
    # a loop with no take: nothing recorded to set the length of
    s.arrangement = Arrangement(bpm=110, cells=[["A2", 0]])
    client.post(
        "/loop/save", data={"lane": "1", "bars": "1", "loop": "riff"}, headers=PERFORM
    )
    r = client.post(
        "/loop/length", data={"name": "riff", "how": "fit"}, headers=PERFORM
    )
    assert r.status_code == 422 and "no take of its own" in r.text
    # under a bar: nothing to trim to
    _take(client, notes=[[0, 1, 60, 1]], bars="0.6", name="keys-short")
    r = client.post(
        "/loop/length", data={"name": "keys-short", "how": "trim"}, headers=PERFORM
    )
    assert r.status_code == 422 and "shorter than a bar" in r.text
    # edited on the grid since: Q is the way back
    _take(client)
    client.post("/loop/edit", data={"name": "keys-1"}, headers=PERFORM)
    client.post("/fragment/pattern/notes/shift_right", headers=PERFORM)
    r = client.post(
        "/loop/length", data={"name": "keys-1", "how": "trim"}, headers=PERFORM
    )
    assert r.status_code == 422 and "edited on the grid" in r.text
