"""Voice lab: take handling and the vocoder render.

Only WAV reaches the server — the browser decodes whatever it was given
(a dragged mp3, MediaRecorder's webm/opus) and uploads PCM — so these tests
exercise the WAV path and the failure modes a bad post can produce.
"""

import io
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio.server import create_app
from synthlab.studio.voice import (
    MAX_TAKE_SECONDS,
    TakeError,
    carrier_for,
    load_take,
    read_wav,
    render_voice,
    save_take,
    take_path,
)
from synthlab.synth import Patch


def _wav_bytes(samples, rate=SR, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        data = np.clip(samples, -1, 1)
        if channels == 2:
            data = np.column_stack([data, data]).ravel()
        w.writeframes((data * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _speech_like(seconds=1.0, with_gate=False):
    """Noise gated into syllables — the shape a vocoder is meant to transfer.
    Returns the gate too when asked: measuring against per-sample amplitude
    is wrong for noise, which dips below any threshold *inside* a syllable."""
    n = int(SR * seconds)
    rng = np.random.default_rng(0)
    # 2 Hz syllables: at 4 Hz the 40ms release tail fills much of each
    # 125ms gap, so the measurement would fight the follower, not the gating
    gate = (np.sin(2 * np.pi * 2 * np.arange(n) / SR) > 0).astype(float)
    sig = rng.standard_normal(n) * 0.4 * gate
    return (sig, gate.astype(bool)) if with_gate else sig


# ---------------------------------------------------------------- take I/O


def test_wav_roundtrip_preserves_signal(tmp_path):
    sig = np.sin(2 * np.pi * 220 * np.arange(SR // 2) / SR) * 0.5
    save_take(tmp_path, sig)
    back = load_take(tmp_path)
    assert back is not None and len(back) == len(sig)
    assert np.allclose(back, sig, atol=1e-3)


# Ground truth for this test: captured by running an earlier, known-good
# `voice.read_wav` on this exact WAV (merging PCM_ENCODE_SCALE and
# PCM_DECODE_SCALE into one constant would move this decode from /32768
# to /32767, a ±1 LSB drift on ~99.99% of samples). The formula is
# `int16 / 32768.0`; pinned as a literal so a
# future change to the decode divisor breaks this test instead of quietly
# drifting the vocoder's take.
_KNOWN_INTS = np.array(
    [0, 1, -1, 100, -100, 16384, -16384, 32767, -32768, 12345, -12345],
    dtype="<i2",
)
_KNOWN_READ_WAV_AT_BASE_208cf0c = [
    0.0,
    3.0517578125e-05,
    -3.0517578125e-05,
    0.0030517578125,
    -0.0030517578125,
    0.5,
    -0.5,
    0.999969482421875,
    -1.0,
    0.376739501953125,
    -0.376739501953125,
]


def test_read_wav_decodes_16bit_pcm_byte_identical_to_pre_task9():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)  # == SR: no resample, decoded values stay exact
        w.writeframes(_KNOWN_INTS.tobytes())
    out = read_wav(buf.getvalue())
    assert np.array_equal(out, np.array(_KNOWN_READ_WAV_AT_BASE_208cf0c))


def test_stereo_upload_is_summed_to_mono():
    sig = np.sin(2 * np.pi * 300 * np.arange(SR // 4) / SR) * 0.4
    mono = read_wav(_wav_bytes(sig, channels=2))
    assert mono.ndim == 1 and len(mono) == len(sig)


def test_other_sample_rates_are_resampled():
    sig = np.sin(2 * np.pi * 200 * np.arange(8000) / 16000) * 0.4
    out = read_wav(_wav_bytes(sig, rate=16000))
    assert abs(len(out) - SR // 2) < 50, len(out)


def test_a_take_is_capped_in_length():
    long = np.zeros(int(SR * (MAX_TAKE_SECONDS + 5)))
    assert len(read_wav(_wav_bytes(long))) <= int(MAX_TAKE_SECONDS * SR)


@pytest.mark.parametrize(
    "raw", [b"", b"not a wav at all", b"RIFF____WAVEfmt " + b"\x00" * 32]
)
def test_unreadable_uploads_raise_take_error(raw):
    """A bad post must become a toast, never a 500."""
    with pytest.raises(TakeError):
        read_wav(raw)


def test_missing_take_reads_as_none(tmp_path):
    assert load_take(tmp_path) is None


def test_corrupt_take_on_disk_reads_as_none_not_a_crash(tmp_path):
    take_path(tmp_path).write_bytes(b"garbage")
    assert load_take(tmp_path) is None


# ----------------------------------------------------------------- render


def test_carrier_is_still_sounding_at_the_end_of_a_long_take():
    """A plucked carrier would silence the back half of any phrase, so the
    carrier is forced to sustain."""
    audio = carrier_for(Patch(decay=0.05, sustain=0.0), seconds=2.0)
    mono = audio.mean(axis=1)
    tail = float(np.sqrt(np.mean(mono[-SR // 4 :] ** 2)))
    head = float(np.sqrt(np.mean(mono[: SR // 4] ** 2)))
    assert tail > head * 0.25, (head, tail)


def test_render_follows_the_takes_rhythm():
    """The point of the whole page: silence in the take is silence out."""
    take, gate = _speech_like(2.0, with_gate=True)
    out = render_voice(Patch(), take, bands=16).mean(axis=1)
    # ignore 60ms either side of every edge: the follower's release is a ramp,
    # and measuring across it measures the ramp rather than the gating
    guard = int(SR * 0.06)
    edge = np.zeros_like(gate)
    edge[:-1] |= gate[:-1] != gate[1:]
    near = np.convolve(edge.astype(float), np.ones(2 * guard + 1), "same") > 0
    on = float(np.sqrt(np.mean(out[gate & ~near] ** 2)))
    off = float(np.sqrt(np.mean(out[~gate & ~near] ** 2)))
    assert on > off * 8, (on, off)


def test_render_is_bounded_and_finite():
    out = render_voice(Patch(), _speech_like(0.5), bands=24)
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) <= 0.9


def test_render_without_a_take_is_a_take_error():
    with pytest.raises(TakeError):
        render_voice(Patch(), np.array([]))


def test_fx_chain_is_applied_to_the_result():
    take = _speech_like(0.4)
    dry = render_voice(Patch(), take, bands=16)
    wet = render_voice(
        Patch(),
        take,
        bands=16,
        chain=[
            __import__("synthlab.effects", fromlist=["x"]).bitcrush(
                bits=4, downsample=8
            )
        ],
    )
    assert not np.allclose(dry, wet, atol=1e-3)


# ----------------------------------------------------------------- routes


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_voice_view_renders(tmp_path):
    r = make_client(tmp_path).get("/view/voice")
    assert r.status_code == 200
    assert 'data-view="voice"' in r.text


def test_upload_then_render_roundtrip(tmp_path):
    client = make_client(tmp_path)
    wav = _wav_bytes(_speech_like(0.6))
    up = client.post("/voice/take", files={"file": ("take.wav", wav, "audio/wav")})
    assert up.status_code == 200, up.text
    r = client.post("/voice/render", data={"bands": "16", "pitch": "A2"})
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")


def test_render_without_a_take_toasts_rather_than_500s(tmp_path):
    r = make_client(tmp_path).post("/voice/render", data={"bands": "16"})
    assert r.status_code == 422
    assert "toast" in r.text


def test_bad_upload_toasts_rather_than_500s(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/voice/take", files={"file": ("x.wav", b"nope", "audio/wav")})
    assert r.status_code == 422
    assert "toast" in r.text


def test_take_is_downloadable_after_upload(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "/voice/take",
        files={"file": ("take.wav", _wav_bytes(_speech_like(0.3)), "audio/wav")},
    )
    r = client.get("/voice/take.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"


def _make_wav(sr: int, frames: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(max(1, sr))
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def test_upload_rejects_hostile_sample_rate(tmp_path):
    client = make_client(tmp_path)
    wav = _make_wav(sr=1, frames=100)
    r = client.post("/voice/take", files={"file": ("j.wav", wav, "audio/wav")})
    assert r.status_code == 422


def test_upload_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_TAKE_UPLOAD_BYTES", 1000)
    client = make_client(tmp_path)
    # A *valid* WAV comfortably over the cap: raw null bytes aren't a real
    # WAV, so a plain (413, 422) check couldn't tell a size rejection from
    # a content rejection. This one only fails for its size.
    wav = _wav_bytes(np.zeros(4000))
    r = client.post("/voice/take", files={"file": ("j.wav", wav, "audio/wav")})
    assert r.status_code == 413


def test_hostile_sample_rate_is_rejected_by_read_wav_directly():
    """Defence in depth: read_wav itself refuses an out-of-range rate
    before it would ever resample toward it."""
    with pytest.raises(TakeError):
        read_wav(_make_wav(sr=1, frames=100))


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("cut", [1, 3])
def test_a_wav_cut_mid_frame_is_a_take_error_not_a_500(tmp_path, channels, cut):
    """A file whose last bytes are gone is not a whole number of frames:
    `np.frombuffer`/`reshape` raised ValueError, which only /voice/take
    let through as a 500 (/clip/upload and /song/take answer 422)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"\x01\x02" * channels * 1000)
    raw = buf.getvalue()[:-cut]
    with pytest.raises(TakeError):
        read_wav(raw)
    client = make_client(tmp_path)
    r = client.post("/voice/take", files={"file": ("t.wav", raw, "audio/wav")})
    assert r.status_code == 422 and "toast" in r.text
