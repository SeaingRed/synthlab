import io
import wave

import numpy as np

from synthlab import audio
from synthlab.core import SR


def _sine_stereo(seconds=0.25, freq=440.0):
    t = np.arange(int(SR * seconds)) / SR
    x = 0.5 * np.sin(2 * np.pi * freq * t)
    return np.column_stack([x, x])


def test_wav_roundtrip_16bit(tmp_path):
    x = _sine_stereo()
    path = str(tmp_path / "t16.wav")
    audio.save(path, x, bit_depth=16)
    y, sr = audio.load(path)
    assert sr == SR
    assert y.shape == x.shape
    assert np.max(np.abs(y - x)) < 2.0 / 32768


def test_wav_roundtrip_32bit(tmp_path):
    x = _sine_stereo()
    path = str(tmp_path / "t32.wav")
    audio.save(path, x, bit_depth=32)
    y, sr = audio.load(path)
    assert np.max(np.abs(y - x)) < 1e-6


def test_wav_roundtrip_24bit_the_default_depth(tmp_path):
    """`save` writes 24-bit unless told otherwise, and `load` refused it
    ("only 16- and 32-bit WAV loading is supported")."""
    x = _sine_stereo() * 1.9  # both signs, up to full scale
    x[0] = (-1.0, 1.0)
    path = str(tmp_path / "t24.wav")
    audio.save(path, x)
    y, sr = audio.load(path)
    assert sr == SR and y.shape == x.shape
    assert np.max(np.abs(y - x)) < 2.0 / 8388608
    assert y[0, 0] < -0.9999 and y[0, 1] > 0.9999  # the sign survives


def test_analyse_reports_sane_numbers():
    x = _sine_stereo(seconds=1.0)
    a = audio.analyse(x)
    assert abs(a["peak"] - 0.5) < 0.01
    assert a["has_nan"] is False
    assert a["clipped_samples"] == 0


def test_wav_roundtrip():
    """The one in-memory WAV codec: wav_bytes/read_pcm16, used in place of
    ad hoc encoders."""
    x = np.linspace(-1, 1, 1000)
    y, sr = audio.read_pcm16(audio.wav_bytes(x, 44100))
    assert sr == 44100 and np.allclose(x, y, atol=1 / 32767)


def test_wav_bytes_preserves_stereo_image():
    x = _sine_stereo(seconds=0.1)
    x[:, 1] *= 0.3  # an asymmetric stereo signal
    y, sr = audio.read_pcm16(audio.wav_bytes(x, sr=44100))
    assert y.shape == x.shape
    assert np.max(np.abs(y - x)) < 2.0 / 32767


def test_wav_bytes_scales_down_hot_signal_instead_of_clipping():
    """The old render._to_wav_bytes "ponytail" headroom logic: a signal
    peaking over 1.0 is scaled down, never hard-clipped, on its way to
    16-bit PCM."""
    hot = _sine_stereo(seconds=0.05) * 3.0  # peaks at 1.5
    raw = audio.wav_bytes(hot)
    pcm = np.frombuffer(raw[44:], dtype="<i2")
    assert pcm.max() <= 32767 and pcm.min() >= -32768
    # scaled to fit the new peak, not clipped flat: only the handful of
    # samples actually at the waveform's peak sit at the ceiling
    at_ceiling = int(np.sum(np.abs(pcm) >= 32760))
    assert 0 < at_ceiling < len(pcm) * 0.05


def test_pcm_scale_constants_are_named_honestly():
    """Encode and decode are NOT unified. save()/wav_bytes() write with
    *32767; load() reads with /32768. Unifying both to 32767 measurably
    drifts ~99.99% of decoded samples by ±1 LSB on the clip lane, drum clip
    samples and the vocoder's take — real (if inaudible) drift the
    no-audio-drift constraint forbids introducing without an explicit
    exception. The constants stay asymmetric, and named."""
    assert audio.PCM_ENCODE_SCALE == 32767.0
    assert audio.PCM_DECODE_SCALE == 32768.0


# Ground truth for the next two tests: captured by checking out commit
# 208cf0c (BASE, immediately before this task) with `git archive` and
# actually running ITS `audio.load()` on this exact WAV. The formula is
# `int16 / 32768.0` — pinned here as a literal array, not recomputed, so a
# future change to the decode divisor breaks this test instead of quietly
# drifting every clip's decoded amplitude.
_KNOWN_INTS = np.array(
    [0, 1, -1, 100, -100, 16384, -16384, 32767, -32768, 12345, -12345],
    dtype="<i2",
)
_KNOWN_DECODED_AT_BASE_208cf0c = [
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


def _known_mono_wav_bytes(sr: int = 44100) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(_KNOWN_INTS.tobytes())
    return buf.getvalue()


def test_load_decodes_16bit_pcm_byte_identical_to_pre_task9(tmp_path):
    path = tmp_path / "known.wav"
    path.write_bytes(_known_mono_wav_bytes())
    y, sr = audio.load(str(path))
    assert sr == 44100
    assert np.array_equal(y[:, 0], np.array(_KNOWN_DECODED_AT_BASE_208cf0c))
