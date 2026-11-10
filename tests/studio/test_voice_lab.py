"""The vocoder: take from the library, four carriers, keep the result
(`studio/voice.py`, `routes/voice.py`).

What is held here: the settings and the take's origin live in
`voice_lab.json` beside the take and nothing a hand-edited one holds
breaks the page; each carrier kind makes audio of the take's length; a
row of chords changes on the bar; a followed carrier is on the sung
notes; a clip of any rate and width is a carrier; "Keep result" saves the
samples the audition played, with a meta saying what made them, and never
writes over a clip; and none of it is session state or part of a render
key.
"""

from __future__ import annotations

import io
import json
import re
import threading
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.audio import read_pcm16
from synthlab.core import SR
from synthlab.sampler import detect_pitch
from synthlab.studio import cliplib, limits, voice
from synthlab.studio.clips import list_clips, save_clip
from synthlab.studio.server import create_app
from synthlab.synth import Patch
from tests.songs import VOICE_LINE, voice_line


def _hx(view: str = "voice") -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


def _wav(samples, rate=SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _speech(seconds: float = 1.0) -> np.ndarray:
    """Noise gated into two syllables a second: words, no pitch."""
    n = int(SR * seconds)
    gate = np.sin(2 * np.pi * 2 * np.arange(n) / SR) > 0
    return np.random.default_rng(0).standard_normal(n) * 0.4 * gate


def _tone(seconds: float = 0.5, hz: float = 220.0, rate: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return np.sin(2 * np.pi * hz * t) * 0.4


@pytest.fixture
def client(tmp_path):
    voice.RESULTS = voice.LRU(4)  # no audition of another test's
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _take(client, samples=None) -> None:
    samples = _speech() if samples is None else samples
    r = client.post(
        "/voice/take", files={"file": ("t.wav", _wav(samples), "audio/wav")}
    )
    assert r.status_code == 200, r.text[:300]


def _render(client, **data):
    return client.post("/voice/render", data=data, headers=_hx())


def _key(r) -> str:
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    return url[len("/preview/") : -len(".wav")]


def _audio(client, r) -> np.ndarray:
    wav = client.get(json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"])
    assert wav.status_code == 200
    return read_pcm16(wav.content)[0]


# --- settings: a form and a file, both hostile -------------------------------------


def test_clean_settings_holds_every_field_to_its_type_and_range():
    assert voice.clean_settings(None) == voice.DEFAULTS
    assert voice.clean_settings([1, 2]) == voice.DEFAULTS
    got = voice.clean_settings(
        {
            "bands": "nan",
            "carrier": 5,
            "notes": "Am  F \n C" + "x" * 10_000,
            "step": "3",
            "snap": ["scale"],
            "octave": 10**400,
            "clip": "../../etc/passwd",
            "shift": "1e999",
            "smooth": True,
            "sibilance": "lots",
            "extra": "ignored",
        }
    )
    assert got["bands"] == 16 and got["carrier"] == "sound"
    assert got["notes"].startswith("Am F C") and "\n" not in got["notes"]
    assert len(got["notes"]) <= limits.MAX_VOICE_NOTES_CHARS
    assert got["step"] == "1" and got["snap"] == "off"
    assert got["octave"] == 0 and got["shift"] == 0.0
    assert got["smooth"] == 40.0 and got["sibilance"] == 0.0
    assert "/" not in got["clip"] and "\\" not in got["clip"] and "extra" not in got
    # numbers are clamped, the band count is the nearest one offered
    got = voice.clean_settings(
        {
            "bands": "30",
            "shift": "-99",
            "smooth": "9999",
            "sibilance": "7",
            "octave": "-9",
        }
    )
    assert (got["bands"], got["shift"], got["smooth"]) == (32, -12.0, 250.0)
    assert (got["sibilance"], got["octave"]) == (1.0, -2)
    assert voice.clean_settings({"notes": "   "})["notes"] == voice.DEFAULT_NOTES


def test_a_form_that_carries_some_fields_keeps_the_rest():
    saved = voice.clean_settings({"notes": "Am F", "bands": 24, "carrier": "follow"})
    got = voice.clean_settings({"bands": "8"}, saved)
    assert (got["bands"], got["notes"], got["carrier"]) == (8, "Am F", "follow")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"take": 3, "settings": "x", "last": 9}',
        '{"take": {"kind": "clip", "name": 4}, "settings": {"bands": [], "clip": 7}}',
        '{"take": {"kind": "clip", "name": "../x", "hash": "<b>x</b>"}, "last": "'
        + "k" * 500
        + '"}',
        "[" * 5000,
    ],
)
def test_a_voice_lab_file_of_any_shape_draws_the_page(client, text):
    """voice_lab.json is a file anyone can edit: whatever it holds, the
    page draws and the render runs."""
    _take(client)
    voice.lab_path(_st(client).data_dir).write_text(text, encoding="utf-8")
    page = client.get("/view/voice")
    assert page.status_code == 200 and 'id="voice-render"' in page.text
    assert (
        "<b>x</b>" not in page.text.split('id="voice-panel"')[1].split("</section>")[0]
    )
    assert _render(client).status_code == 200


def test_the_panel_keeps_what_was_rendered_across_a_reload(client):
    """It used to come back with 16 bands and A2 after every render."""
    _take(client)
    r = _render(
        client,
        bands="24",
        notes="Am F C G",
        step="2",
        shift="5",
        smooth="120",
        sibilance="0.4",
    )
    assert r.status_code == 200
    for html in (r.text, client.get("/view/voice").text):
        assert 'value="Am F C G"' in html
        assert '<option value="24" selected>' in html
        assert '<option value="2" selected>' in html
        assert re.search(r'name="shift" value="5"', html)
        assert re.search(r'name="smooth" value="120"', html)
        assert re.search(r'name="sibilance" value="0.4"', html)
    assert voice.read_lab(_st(client).data_dir)["settings"]["notes"] == "Am F C G"


def test_an_old_pages_one_carrier_note_still_renders(client):
    _take(client)
    r = _render(client, bands="8", pitch="E2")
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    assert voice.read_lab(_st(client).data_dir)["settings"]["notes"] == "E2"


# --- the take remembers where it came from ------------------------------------------


def test_the_take_names_the_clip_it_came_from_and_a_recording_forgets_it(client):
    d = _st(client).data_dir
    save_clip(d, "verse 1", _speech(0.5), meta={"source": "voice take"})
    r = client.post("/voice/take/clip", data={"name": "verse 1"}, headers=_hx())
    assert r.status_code == 200 and "clip “verse 1”" in r.text
    origin = voice.read_lab(d)["take"]
    assert origin["kind"] == "clip" and origin["name"] == "verse 1" and origin["hash"]
    assert "clip “verse 1”" in client.get("/view/voice").text
    _take(client)
    assert voice.read_lab(d)["take"] == {"kind": "recording", "name": "", "hash": ""}
    assert "a recording" in client.get("/view/voice").text


def test_a_new_take_keeps_what_the_form_held(client):
    """voice.js sends the vocoder's form with the file, and the drawer's
    buttons include it, so the panel that comes back has not forgotten."""
    d = _st(client).data_dir
    r = client.post(
        "/voice/take",
        data={"notes": "Dm7 G7", "bands": "32", "carrier": "sound"},
        files={"file": ("t.wav", _wav(_speech(0.3)), "audio/wav")},
    )
    assert r.status_code == 200 and 'value="Dm7 G7"' in r.text
    save_clip(d, "hum", _tone())
    r = client.post(
        "/voice/take/clip", data={"name": "hum", "notes": "C E G"}, headers=_hx()
    )
    assert 'value="C E G"' in r.text
    assert voice.read_lab(d)["settings"]["bands"] == 32


# --- carriers -----------------------------------------------------------------------


def _hz(x) -> float:
    return 440.0 * 2 ** ((detect_pitch(x.mean(axis=1))[0] - 69) / 12)


def test_one_chord_lasts_the_take_and_a_row_changes_on_the_bar():
    n = SR * 2
    one = voice.chord_carrier(Patch(), [[45]], n, 120.0, "1")
    assert one.shape == (n, 2)
    assert abs(detect_pitch(one[SR // 2 : SR].mean(axis=1))[0] - 45) < 0.1
    assert abs(detect_pitch(one[-SR // 2 :].mean(axis=1))[0] - 45) < 0.1
    # at 120 bpm half a bar is a second: A2, then E3, then A2 again
    row = voice.chord_carrier(Patch(), [[45], [52]], SR * 3, 120.0, "0.5")
    got = [
        detect_pitch(row[i * SR + 4000 : (i + 1) * SR - 4000].mean(axis=1))[0]
        for i in range(3)
    ]
    assert np.allclose(got, [45, 52, 45], atol=0.1), got
    # "even": the take shared out once through
    even = voice.chord_carrier(Patch(), [[45], [52]], SR * 3, 120.0, "even")
    assert abs(detect_pitch(even[SR // 4 : SR].mean(axis=1))[0] - 45) < 0.1
    assert abs(detect_pitch(even[2 * SR : -SR // 4].mean(axis=1))[0] - 52) < 0.1


def test_a_rest_is_silence_and_a_chord_sounds_all_its_notes():
    n = SR * 2
    row = voice.chord_carrier(Patch(osc1="sine"), [[57, 61, 64], []], n, 120.0, "0.5")
    assert float(np.max(np.abs(row[SR + 8000 :]))) < 1e-3  # the rest, past the release
    mag = np.abs(np.fft.rfft(row[2000 : SR - 2000, 0] * np.hanning(SR - 4000)))
    freqs = np.fft.rfftfreq(SR - 4000, 1 / SR)
    for midi in (57, 61, 64):
        hz = 440.0 * 2 ** ((midi - 69) / 12)
        near = mag[(freqs > hz * 0.98) & (freqs < hz * 1.02)].max()
        assert near > 0.2 * mag.max(), midi
    # a typed chord of any size is cut to what sounds; a note is moved by
    # octaves into range, never clamped to another note
    big = voice.chord_carrier(Patch(), [list(range(40, 60))], 4000, 120.0, "1")
    assert big.shape == (4000, 2) and np.all(np.isfinite(big))
    assert voice._fit_midi(200) % 12 == 200 % 12 and voice._fit_midi(-7) % 12 == 5


def test_a_followed_carrier_is_on_the_sung_notes():
    """The carrier's pitch, measured on the carrier itself, is the sung
    line's: within 20 cents of each held note (measured: 4)."""
    line = voice_line().mean(axis=1)
    for classes, octave in ((None, 0), ([0, 2, 4, 5, 7, 9, 11], 0), (None, -1)):
        carrier, share = voice.follow_carrier(Patch(), line, classes, octave)
        assert carrier.shape == (len(line), 2) and 0.6 < share < 0.9
        at = 0.0
        for note, seconds in VOICE_LINE:
            if note is not None:
                a, b = int((at + 0.1) * SR), int((at + seconds - 0.1) * SR)
                want = (round(note) if classes else note) + 12 * octave
                got, sure = detect_pitch(carrier[a:b].mean(axis=1))
                assert sure > 0.9 and abs(got - want) < 0.2, (note, got)
            at += seconds


def test_following_a_take_with_no_pitch_is_refused_in_words(client):
    with pytest.raises(voice.TakeError):
        voice.follow_carrier(Patch(), _speech(), None)
    _take(client)
    r = _render(client, carrier="follow")
    assert r.status_code == 422 and "No clear pitch" in r.text


@pytest.mark.parametrize("rate", [SR, 48_000, 22_050])
@pytest.mark.parametrize("stereo", [False, True])
def test_a_clip_of_any_rate_and_width_is_a_carrier(tmp_path, rate, stereo):
    audio = _tone(0.5, 330.0, rate)
    audio = np.column_stack([audio, audio * 0.5]) if stereo else audio
    save_clip(tmp_path, "pad", audio, sr=rate)
    got = voice.load_carrier_clip(tmp_path, "pad")
    assert got.ndim == 2 and got.shape[1] == 2  # the store keeps stereo
    assert abs(len(got) - SR // 2) <= 2
    assert abs(_hz(voice.tiled(got, SR // 2)) - 330.0) < 3.0
    # shorter than the take: repeated; longer: cut; always stereo, exactly n
    for n in (SR // 5, SR // 2 + 77, SR * 2):
        t = voice.tiled(got, n, loop=False)
        assert t.shape == (n, 2) and np.all(np.isfinite(t))
    assert voice.tiled(np.zeros((0, 2)), 100).shape == (100, 2)


def test_a_repeated_clip_that_is_no_loop_does_not_click_at_the_seam():
    x = np.ones((1000, 2)) * 0.5  # the worst case: it ends where it does not start
    hard = voice.tiled(x, 2500, loop=True)
    soft = voice.tiled(x, 2500, loop=False)
    assert np.all(hard == 0.5)  # a loop is repeated as it is
    assert soft[0, 0] == 0.0 and abs(soft[999, 0]) < 0.01 and soft[1000, 0] == 0.0
    assert float(np.max(np.abs(np.diff(soft[:, 0])))) < 0.01


@pytest.mark.parametrize(
    "name", ["", "nope", "../state", "..\\..\\x", "a" * 500, "con", "\x00"]
)
def test_a_carrier_clip_that_is_not_there_is_refused(client, name):
    with pytest.raises(voice.TakeError):
        voice.load_carrier_clip(_st(client).data_dir, name)
    r = client.post("/voice/carrier/clip", data={"name": name}, headers=_hx())
    assert r.status_code == 422 and "toast" in r.text
    assert voice.read_lab(_st(client).data_dir)["settings"]["carrier"] == "sound"


def test_a_carrier_clip_with_a_hostile_header_is_refused(client):
    d = _st(client).data_dir
    (d / "clips").mkdir(exist_ok=True)
    (d / "clips" / "junk.wav").write_bytes(b"RIFF nope")
    (d / "clips" / "slow.wav").write_bytes(_wav(np.zeros(100), rate=1))
    for name in ("junk", "slow"):
        r = client.post("/voice/carrier/clip", data={"name": name}, headers=_hx())
        assert r.status_code == 422, name


# --- the render route ---------------------------------------------------------------


def test_every_carrier_kind_renders_the_takes_length_and_they_differ(client):
    d = _st(client).data_dir
    line = voice_line().mean(axis=1)
    _take(client, line)
    save_clip(d, "pad", np.column_stack([_tone(0.4, 180.0), _tone(0.4, 181.0)]))
    assert client.post("/voice/carrier/clip", data={"name": "pad"}).status_code == 200
    out = {}
    for kind in voice.CARRIERS:
        r = _render(client, carrier=kind, notes="A2 E3")
        assert r.status_code == 200, (kind, r.text[:200])
        out[kind] = _audio(client, r)
        assert out[kind].shape == (len(line), 2), kind
        assert 0.85 < float(np.max(np.abs(out[kind]))) <= 0.9, kind
        # silence in the take is silence out: the gap after the first note
        gap = out[kind][int(0.42 * SR) : int(0.55 * SR)]
        assert float(np.sqrt(np.mean(gap**2))) < 0.02, kind
    kinds = list(out)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1 :]:
            assert not np.allclose(out[a], out[b], atol=1e-2), (a, b)
    # a stereo carrier clip gives a stereo result; the sound's is centred
    assert not np.allclose(out["clip"][:, 0], out["clip"][:, 1], atol=1e-3)


def test_carrier_notes_that_cannot_be_read_are_a_toast(client):
    _take(client)
    for notes in ("Hm7", "C4 zzz", " ".join(["C4"] * 70), "<script>"):
        r = _render(client, notes=notes)
        assert r.status_code == 422 and "Carrier notes" in r.text, notes
        assert "<script>" not in r.text
    assert _render(client, notes="~ ~").status_code == 422  # all rests: silent
    assert "silent" in _render(client, notes="~").text


def test_numerals_are_read_in_the_sessions_key(client):
    _take(client)
    a = _audio(client, _render(client, notes="i"))
    b = _audio(client, _render(client, notes="Am"))
    c = _audio(client, _render(client, notes="V"))
    assert a.shape == b.shape and not np.allclose(a, c, atol=1e-2)


def test_the_controls_reach_the_vocoder(client, monkeypatch):
    seen = {}
    real = fx.vocoder

    def spy(carrier, mod, **kw):
        seen.update(kw)
        return real(carrier, mod, **kw)

    monkeypatch.setattr(fx, "vocoder", spy)
    _take(client)
    r = _render(client, bands="24", shift="-7", smooth="100", sibilance="0.5")
    assert r.status_code == 200
    assert seen == {"bands": 24, "release": 0.1, "shift": -7.0, "noise": 0.5}


def test_the_same_render_is_answered_from_what_is_held(client, monkeypatch):
    _take(client)
    first = _render(client, bands="8")
    calls = []
    monkeypatch.setattr(fx, "vocoder", lambda *a, **k: calls.append(1))
    again = _render(client, bands="8")
    assert again.status_code == 200 and _key(again) == _key(first) and not calls
    # ...and anything the audio depends on is a new key
    monkeypatch.undo()
    assert _key(_render(client, bands="16")) != _key(first)
    _st(client).patch = _st(client).patch.tweak(cutoff=900.0)
    assert _key(_render(client, bands="8")) != _key(first)
    assert _key(first).startswith("voice:") and len(_key(first)) == 30


def test_the_vocoding_runs_with_the_studio_lock_released(client, monkeypatch):
    """Seconds for a long take: another request must not wait behind it."""
    s = _st(client)
    real, held = fx.vocoder, []

    def other() -> None:  # an RLock: taken and given back by the one thread
        got = s.lock.acquire(timeout=2)
        held.append(got)
        if got:
            s.lock.release()

    def spy(*a, **k):
        t = threading.Thread(target=other)
        t.start()
        t.join()
        return real(*a, **k)

    monkeypatch.setattr(fx, "vocoder", spy)
    _take(client)
    assert _render(client).status_code == 200
    assert held == [True]  # another thread took the lock mid-render


def test_a_render_says_it_is_running(client, monkeypatch):
    """A progress line for the pages that are listening (`events.Task`)."""
    said = []
    bus = client.app.state.svc.events
    monkeypatch.setattr(type(bus), "active", property(lambda self: True))
    monkeypatch.setattr(
        bus, "publish", lambda kind, data, **kw: said.append((kind, data))
    )
    _take(client)
    assert _render(client).status_code == 200
    kinds = [k for k, d in said if d.get("label") == "Vocoding"]
    assert kinds[0] == "progress" and kinds[-1] == "done", kinds


def test_without_a_take_it_is_a_toast(client):
    assert _render(client).status_code == 422
    r = client.post("/voice/result/keep", data={}, headers=_hx())
    assert r.status_code == 422 and "Vocode it again" in r.text


# --- keep the result ------------------------------------------------------------------


def test_keep_saves_the_samples_the_audition_played(client):
    d = _st(client).data_dir
    save_clip(d, "verse 1", _speech(1.0), meta={"source": "voice take"})
    client.post("/voice/take/clip", data={"name": "verse 1"}, headers=_hx())
    r = _render(client, notes="Am F", step="0.5", bands="8", sibilance="0.3")
    heard = _audio(client, r)
    assert 'hx-post="/voice/result/keep"' in r.text and "Keep result" in r.text
    assert "“verse 1” over" in r.text
    # the sound moves on after the audition: Keep still saves what was heard
    _st(client).patch = _st(client).patch.tweak(cutoff=500.0)
    k = client.post("/voice/result/keep", data={"key": _key(r)}, headers=_hx())
    assert k.status_code == 200 and k.headers["X-Clip-Name"] == "verse 1 vocoded"
    wav = (d / "clips" / "verse 1 vocoded.wav").read_bytes()
    assert np.array_equal(read_pcm16(wav)[0], heard)
    meta = json.loads((d / "clips" / "verse 1 vocoded.meta.json").read_text("utf-8"))
    assert meta["source"] == "vocoder" and meta["bpm"] == _st(client).arrangement.bpm
    voc = meta["vocoder"]
    assert voc["take"]["name"] == "verse 1" and voc["take"]["hash"]
    assert voc["carrier"] == {
        "kind": "sound",
        "sound": _st(client).patch.name,
        "notes": "Am F",
        "step": "0.5",
    }
    assert voc["settings"] == {
        "bands": 8,
        "shift": 0.0,
        "smooth": 40.0,
        "sibilance": 0.3,
    }
    assert voc["seconds"] == 1.0
    # the library comes back out of band with the Vocoder group in it
    assert 'id="clip-library"' in k.text and 'data-group="vocoder"' in k.text


def test_keep_never_writes_over_a_clip(client):
    d = _st(client).data_dir
    _take(client)
    r = _render(client)
    names = []
    for _ in range(3):
        k = client.post(
            "/voice/result/keep", data={"key": _key(r), "name": "robot"}, headers=_hx()
        )
        names.append(k.headers["X-Clip-Name"])
    assert names == ["robot", "robot 2", "robot 3"]
    assert {c["name"] for c in list_clips(d)} == set(names)
    # no key posted: the last audition
    k = client.post("/voice/result/keep", data={}, headers=_hx())
    assert k.headers["X-Clip-Name"] == "vocoded"  # a recording has no name to lend


def test_a_result_no_longer_held_is_a_toast_not_another_render(client):
    _take(client)
    r = _render(client)
    voice.RESULTS = voice.LRU(4)  # a restart
    k = client.post("/voice/result/keep", data={"key": _key(r)}, headers=_hx())
    assert k.status_code == 422 and "Vocode it again" in k.text
    assert list_clips(_st(client).data_dir) == []
    assert "/voice/result/keep" not in client.get("/view/voice").text
    for key in ("", "voice:" + "0" * 24, "../../x", "k" * 5000):
        k = client.post("/voice/result/keep", data={"key": key}, headers=_hx())
        assert k.status_code == 422


def test_a_result_states_its_tempo_only_when_its_carrier_has_one(client):
    d = _st(client).data_dir
    s = _st(client)
    bar = 4 * 60.0 / s.arrangement.bpm
    _take(client, _speech(2 * bar))  # two bars of the session, to the sample

    def kept(**data) -> dict:
        r = _render(client, **data)
        assert r.status_code == 200, r.text[:200]
        k = client.post("/voice/result/keep", data={"key": _key(r)}, headers=_hx())
        name = k.headers["X-Clip-Name"]
        return json.loads((d / "clips" / f"{name}.meta.json").read_text("utf-8"))

    one = kept(carrier="sound", notes="A2")
    assert "bpm" not in one and "bars" not in one  # one held note has no clock
    row = kept(carrier="sound", notes="Am F")
    assert row["bpm"] == s.arrangement.bpm and row["bars"] == 2
    assert "bpm" not in kept(carrier="sound", notes="Am F", step="even")
    assert kept(carrier="loop")["bars"] == 2
    save_clip(
        d, "beat", np.column_stack([_tone(1.0)] * 2), meta={"bpm": 90.0, "bars": 1}
    )
    save_clip(d, "pad", np.column_stack([_tone(1.0)] * 2), meta={"source": "x"})
    client.post("/voice/carrier/clip", data={"name": "beat"})
    assert kept(carrier="clip")["bpm"] == 90.0
    client.post("/voice/carrier/clip", data={"name": "pad"})
    assert "bpm" not in kept(carrier="clip")
    # every one of them is in the Vocoder group, tempo or not
    groups = cliplib.groups(list_clips(d))
    voc = next(g for g in groups if g["key"] == "vocoder")
    assert len(voc["clips"]) == 6 and voc["label"] == "Vocoder"


def test_result_meta_counts_bars_within_ten_ms():
    origin = voice.clean_origin(None)
    st = voice.clean_settings({})
    bar = int(SR * 2.0)  # 120 bpm
    assert voice.result_meta(origin, st, {}, 2 * bar, 120.0)["bars"] == 2
    assert voice.result_meta(origin, st, {}, 2 * bar + 400, 120.0)["bars"] == 2
    assert "bars" not in voice.result_meta(origin, st, {}, 2 * bar + 900, 120.0)
    assert "bars" not in voice.result_meta(origin, st, {}, bar // 3, 120.0)
    assert "bpm" not in voice.result_meta(origin, st, {}, bar, None)


def test_a_vocoder_meta_of_any_shape_groups_and_never_fails(client):
    d = _st(client).data_dir
    for i, meta in enumerate(
        [
            {"vocoder": {}},
            {"vocoder": "x"},
            {"source": "vocoder"},
            {"vocoder": {"take": 3}},
        ]
    ):
        save_clip(d, f"v{i}", _tone(0.1), meta=meta)
    groups = {
        g["key"]: [c["name"] for c in g["clips"]] for g in cliplib.groups(list_clips(d))
    }
    assert groups["vocoder"] == ["v0", "v2", "v3"] and groups["other"] == ["v1"]
    assert client.get("/view/voice").status_code == 200


# --- what none of it touches --------------------------------------------------------


def test_the_vocoder_is_no_session_state_and_no_render_key(client):
    """A session saves the same bytes and renders under the same keys
    whether or not the Voice lab was used: the take, its settings and the
    results are files beside the session."""
    svc = client.app.state.svc
    d = svc.state.data_dir
    before = (
        svc.render_inputs("perform").key(),
        svc.render_inputs("editor").key(),
        json.dumps(svc.state._payload()),
    )
    save_clip(d, "pad", _tone(0.4))
    line = voice_line().mean(axis=1)
    _take(client, line)
    client.post("/voice/take/clip", data={"name": "pad"}, headers=_hx())
    client.post("/voice/carrier/clip", data={"name": "pad"}, headers=_hx())
    _take(client, line)
    for kind in voice.CARRIERS:
        r = _render(client, carrier=kind, notes="Am F C G", sibilance="0.5", shift="3")
        assert r.status_code == 200, kind
        client.post("/voice/result/keep", data={"key": _key(r)}, headers=_hx())
    after = (
        svc.render_inputs("perform").key(),
        svc.render_inputs("editor").key(),
        json.dumps(svc.state._payload()),
    )
    assert before == after
    assert voice.lab_path(d).is_file() and len(list_clips(d)) == 5


def test_two_renders_in_flight_both_answer(client):
    """A double click: both vocode with the lock released and both write
    the last audition's key to voice_lab.json — one writer at a time."""
    _take(client)
    got = []

    def press(bands: str) -> None:
        got.append(_render(client, bands=bands).status_code)

    threads = [threading.Thread(target=press, args=(b,)) for b in ("8", "16", "24")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got == [200, 200, 200]
    lab = voice.read_lab(_st(client).data_dir)
    assert lab["last"].startswith("voice:") and voice.RESULTS.get(lab["last"])


# --- presets (the loose ends: `voice.PRESETS` had data and no control) -----------


def test_the_panel_offers_each_preset_as_a_command(client):
    page = client.get("/view/voice").text
    for key, preset in voice.PRESETS.items():
        assert f'data-cmd="Voice preset: {preset["label"]}"' in page
        assert f'"name": "{key}"' in page


def test_a_preset_sets_the_vocoder_and_loads_its_carrier_without_a_take(client):
    s = _st(client)
    s.patch = Patch(name="mine")
    was = voice.read_lab(s.data_dir)["settings"]
    # the form rides along: what was typed and the preset does not set stays
    r = client.post("/voice/preset", data={"name": "talkbox", "notes": "Am F C G"})
    assert r.status_code == 200, r.text[:300]
    st = voice.read_lab(s.data_dir)["settings"]
    want = voice.PRESETS["talkbox"]["settings"]
    assert {k: st[k] for k in want} == want and st != was
    assert st["notes"] == "Am F C G"
    # the carrier it was made for is the sound on Synths now, one undo away
    assert s.patch.name == voice.PRESETS["talkbox"]["sound"]
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "Talkbox" in said and "Ctrl+Z" in said
    assert 'id="voice-panel"' in r.text and "No take yet" in r.text
    assert s.pop_history() and s.patch.name == "mine"


def test_an_unknown_preset_is_a_toast_and_changes_nothing(client):
    s = _st(client)
    was = voice.read_lab(s.data_dir)["settings"]
    r = client.post("/voice/preset", data={"name": "robot9000"})
    assert r.status_code == 422 and "No such Voice preset" in r.text
    assert voice.read_lab(s.data_dir)["settings"] == was
