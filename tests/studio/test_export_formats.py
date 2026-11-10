"""Export formats (`audio.encode`, `?format=` on `/export/song.wav` and
`/export/stems.zip`): FLAC is the WAV's own samples, MP3 a lossy copy of
them, and the WAV is the bytes it always was."""

from __future__ import annotations

import io
import sys
import wave
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import audio
from synthlab.core import SR
from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.render import _to_wav_bytes
from synthlab.studio.server import create_app
from synthlab.synth import Patch

sf = pytest.importorskip("soundfile")  # the studio extra: the codecs under test

SHORT = Patch(name="short", release=0.05).to_json()


def _song() -> dict:
    song = sm.new_song(120.0, "A1", "minor")
    lead = sm.add_track(song, "synth", "Lead", patch=SHORT)
    drums = sm.add_track(song, "drums", "Kit")
    verse = sm.add_section(song, "Verse", 1)
    riff = sm.add_pattern(
        song,
        "notes",
        "r",
        {"cells": [["A2", 0], ["E3", 2]], "bars": 1, "steps_per_bar": 4},
    )
    beat = sm.add_pattern(
        song, "drums", "b", {"drums": [["kick", 0], ["snare", 8]], "bars": 1}
    )
    sm.set_cell(song, verse, lead, riff)
    sm.set_cell(song, verse, drums, beat)
    return song


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        s.song = schema.conform(_song(), schema.nodes().SONG)
        yield c


def _pcm(data: bytes) -> np.ndarray:
    """16-bit WAV bytes -> the raw int16 frames, (n, 2)."""
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 2
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, 2)


def _decode(data: bytes) -> tuple[np.ndarray, int]:
    return sf.read(io.BytesIO(data), dtype="int16")


def _tone(seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    left = 0.5 * np.sin(2 * np.pi * 220 * t) + 0.2 * np.sin(2 * np.pi * 3300 * t)
    right = 0.4 * np.sin(2 * np.pi * 330 * t)
    return np.column_stack([left, right]) * np.minimum(1.0, t * 20)[:, None]


def test_wav_through_encode_is_wav_bytes():
    x = _tone()
    assert audio.encode(x, "wav") == audio.wav_bytes(x)
    assert audio.encode(x * 3, "wav") == audio.wav_bytes(x * 3)  # the peak rule too


def test_flac_decodes_to_exactly_the_wavs_samples():
    """Lossless means the WAV's own 16-bit numbers, a hot signal's peak
    scaling included."""
    for x in (_tone(), _tone() * 3):
        data = audio.encode(x, "flac")
        assert data[:4] == b"fLaC"
        got, sr = _decode(data)
        assert sr == SR
        assert np.array_equal(got, _pcm(audio.wav_bytes(x)))
        assert len(data) < len(audio.wav_bytes(x))


def test_mp3_decodes_to_within_tolerance_of_the_wav():
    """Lossy, but the same audio: the same length to within the codec's
    padding, the same level, and an error far under the signal."""
    x = _tone()
    wav = _pcm(audio.wav_bytes(x)).astype(np.float64) / 32768
    data = audio.encode(x, "mp3")
    got, sr = sf.read(io.BytesIO(data))
    assert sr == SR and got.shape[1] == 2
    assert abs(len(got) - len(wav)) <= 2 * 1152  # MP3 frames
    n = min(len(got), len(wav))
    err = got[:n] - wav[:n]
    rms = float(np.sqrt(np.mean(wav[:n] ** 2)))
    assert float(np.sqrt(np.mean(err**2))) < 0.05 * rms
    assert len(data) < len(audio.wav_bytes(x)) / 4


def test_an_unknown_format_is_a_key_error_and_a_missing_codec_says_so(monkeypatch):
    with pytest.raises(KeyError):
        audio.encode(_tone(0.1), "ogg; rm -rf")
    monkeypatch.setitem(sys.modules, "soundfile", None)  # import fails
    with pytest.raises(audio.CodecUnavailable, match="soundfile"):
        audio.encode(_tone(0.1), "flac")
    assert audio.encode(_tone(0.1), "wav")[:4] == b"RIFF"  # WAV needs nothing


def test_the_wav_export_is_byte_identical_with_or_without_the_choice(client):
    svc = client.app.state.svc
    plain = client.get("/export/song.wav")
    assert plain.status_code == 200
    assert plain.headers["content-type"] == "audio/wav"
    assert 'filename="synthlab-song.wav"' in plain.headers["content-disposition"]
    assert plain.content == client.get("/export/song.wav?format=wav").content
    # and it is the encoder it always was, over the same mixdown
    song, master = svc.song_snapshot()
    assert plain.content == _to_wav_bytes(svc.song_mixdown(song, master))


@pytest.mark.parametrize(
    ("fmt", "media", "magic"),
    [("flac", "audio/flac", b"fLaC"), ("mp3", "audio/mpeg", None)],
)
def test_the_song_exports_in_each_format_with_its_type_and_name(
    client, fmt, media, magic
):
    wav = _pcm(client.get("/export/song.wav").content)
    r = client.get(f"/export/song.wav?format={fmt}")
    assert r.status_code == 200
    assert r.headers["content-type"] == media
    assert f'filename="synthlab-song.{fmt}"' in r.headers["content-disposition"]
    if magic:
        assert r.content[:4] == magic
    got, sr = _decode(r.content)
    assert sr == SR
    if fmt == "flac":
        assert np.array_equal(got, wav)
    else:
        n = min(len(got), len(wav))
        assert abs(len(got) - len(wav)) <= 2 * 1152
        a, b = got[:n].astype(np.float64), wav[:n].astype(np.float64)
        assert np.sqrt(np.mean((a - b) ** 2)) < 0.1 * np.sqrt(np.mean(b**2))


def test_the_stems_zip_takes_the_format_too(client):
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        wavs = {name: _pcm(z.read(name)) for name in z.namelist()}
    assert sorted(wavs) == ["drums.wav", "synth-1.wav"]
    r = client.get("/export/stems.zip?format=flac")
    assert r.status_code == 200
    assert 'filename="synthlab-stems-flac.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["drums.flac", "synth-1.flac"]
        for name in z.namelist():
            got, _sr = _decode(z.read(name))
            assert np.array_equal(got, wavs[name[:-5] + ".wav"])


class TickingClock:
    """The `time` module as zipfile sees it, two seconds later (a zip
    timestamp's resolution) every time it is asked for the local time."""

    def __init__(self):
        import time

        self._time, self.asked = time, 0

    def __getattr__(self, name):
        return getattr(self._time, name)

    def localtime(self, *a):
        self.asked += 1
        return self._time.localtime(self._time.time() + 2 * self.asked)


def test_a_stems_zip_is_the_same_bytes_whenever_it_is_made(client, monkeypatch):
    """`z.writestr(name, ...)` stamps each entry with the wall clock, at
    2 s resolution: two exports of one song differed whenever they
    straddled a tick (and a test comparing them failed now and then)."""
    from synthlab.studio.files import ZIP_EPOCH

    monkeypatch.setattr(zipfile, "time", TickingClock())
    first = client.get("/export/stems.zip").content
    assert client.get("/export/stems.zip").content == first
    with zipfile.ZipFile(io.BytesIO(first)) as z:
        infos = z.infolist()
    assert infos and all(i.date_time == ZIP_EPOCH for i in infos)
    assert all(i.compress_type == zipfile.ZIP_STORED for i in infos)
    assert {(i.create_system, i.external_attr) for i in infos} == {(0, 0o600 << 16)}


@pytest.mark.parametrize("url", ["/export/song.wav", "/export/stems.zip"])
@pytest.mark.parametrize("fmt", ["ogg", "FLAC", "", "../x", "wav\r\nX-A: b"])
def test_a_format_off_the_whitelist_is_a_422_before_anything_renders(
    client, monkeypatch, url, fmt
):
    svc = client.app.state.svc
    monkeypatch.setattr(svc, "_song_raw", lambda *a, **k: pytest.fail("rendered"))
    r = client.get(url, params={"format": fmt})
    assert r.status_code == 422 and "wav, flac, mp3" in r.text


def test_a_machine_without_the_codec_gets_a_toast_not_a_500(client, monkeypatch):
    svc = client.app.state.svc
    monkeypatch.setitem(sys.modules, "soundfile", None)
    monkeypatch.setattr(svc, "_song_raw", lambda *a, **k: pytest.fail("rendered"))
    r = client.get("/export/song.wav?format=mp3")
    assert r.status_code == 422 and "not available on this machine" in r.text
    monkeypatch.undo()
    assert client.get("/export/song.wav").status_code == 200  # WAV still works


def test_a_loudness_target_and_a_format_ride_the_same_export(client):
    """The Song topbar's one form sends both: the mastered audio, in the
    format asked for, named for what it measures."""
    resp = client.get("/export/song.wav?loudness=streaming&format=flac")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/flac"
    assert "LUFS).flac" in resp.headers["content-disposition"]
    assert "x-loudness-lufs" in resp.headers
    plain = client.get("/export/song.wav?format=flac")
    assert _decode(resp.content)[0].shape[1] == 2
    assert resp.content != plain.content
