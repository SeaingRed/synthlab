"""
synthlab.audio — getting sound out of the box.

save()   write a WAV (16/24/32-bit); load() reads all three back
play()   real-time playback through the sound card (needs `sounddevice`)
stream() run a synth callback live, for building instruments you can jam on
analyse() quick numbers: peak, RMS, LUFS-ish loudness, DC offset
"""

from __future__ import annotations

import io
import wave

import numpy as np

from .core import SR
from .effects import to_stereo

# PCM16 <-> float scale. These are asymmetric ON PURPOSE and stay that way:
# every 16-bit encoder in this codebase writes with `* 32767`, and every
# 16-bit decoder except the in-memory one below reads with `/ 32768`.
# Unifying both to 32767 is measurably wrong: it moves ~99.99% of decoded
# samples by ±1 LSB on the clip lane, drum clip samples and the vocoder's
# take, which is real (if inaudible) drift the no-audio-drift constraint
# forbids introducing outside an explicit exception.
PCM_ENCODE_SCALE = 32767.0  # save(), wav_bytes(), voice.save_take()
PCM_DECODE_SCALE = 32768.0  # load(), voice.read_wav()


def normalize(x: np.ndarray, peak: float = 0.89) -> np.ndarray:
    m = np.max(np.abs(x)) or 1.0
    return x / m * peak


def wav_bytes(audio: np.ndarray, sr: int = SR) -> bytes:
    """Mono `(n,)` or stereo `(n, 2)` float array -> 16-bit PCM WAV bytes,
    in memory, same channel count as given. The one in-memory WAV encoder
    (`render._to_wav_bytes` and several ad hoc `server.py` encoders used
    to each do this by hand — this pairs with `read_pcm16`).

    A hot patch (osc1+osc2+sub+unison) can peak well over 1.0; clipping
    would bake distortion into the WAV, so a peak above 1.0 is scaled back
    to fit instead — anything already under 1.0 (loops are normalized to
    0.89) is untouched.
    """
    pcm = _pcm16(audio)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2 if pcm.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _pcm16(audio: np.ndarray) -> np.ndarray:
    """The 16-bit samples `wav_bytes` writes (its peak rule included), so
    every other encoder below starts from the WAV's own numbers."""
    x = np.asarray(audio)
    if not np.issubdtype(x.dtype, np.floating):
        x = x.astype(np.float64)
    peak = max(float(x.max()), -float(x.min())) if x.size else 0.0
    out = np.empty(x.shape, dtype="<i2")
    # a piece at a time, in float64: the arithmetic of one whole-array pass,
    # sample for sample, without two more float64 copies of a whole song
    # (an eight-minute export held 680 MB of them here)
    for a in range(0, len(x), _PCM_BLOCK):
        piece = np.asarray(x[a : a + _PCM_BLOCK], dtype=np.float64)
        if peak > 1.0:
            piece = piece / peak
        out[a : a + _PCM_BLOCK] = (piece * PCM_ENCODE_SCALE).astype("<i2")
    return out


_PCM_BLOCK = 1 << 18  # frames `_pcm16` converts at a time


class CodecUnavailable(RuntimeError):
    """A compressed format was asked for and `soundfile` (libsndfile) is
    not installed or was built without it; str(err) says which."""


# What `encode` writes: name -> (media type, file extension, libsndfile
# format, subtype). WAV is `wav_bytes`, with no library behind it.
EXPORT_FORMATS = {
    "wav": ("audio/wav", "wav", None, None),
    "flac": ("audio/flac", "flac", "FLAC", "PCM_16"),
    "mp3": ("audio/mpeg", "mp3", "MP3", "MPEG_LAYER_III"),
}
# LAME's VBR quality through libsndfile: 0 is the best it has, 1 the
# smallest file.
MP3_COMPRESSION = 0.2


def codec(fmt: str):
    """The library that writes `fmt`: None for "wav" (nothing is needed),
    else the `soundfile` module. Raises `KeyError` for a format that is not
    in `EXPORT_FORMATS` and `CodecUnavailable` when the library, or the
    format in the library found, is missing — a caller about to render
    minutes of audio asks this first."""
    sf_format = EXPORT_FORMATS[fmt][2]
    if sf_format is None:
        return None
    try:
        import soundfile as sf
    except (ImportError, OSError) as err:  # OSError: libsndfile failed to load
        raise CodecUnavailable(
            f"{fmt.upper()} export needs the soundfile package, which is not "
            "available on this machine (uv sync --all-extras installs it)."
        ) from err
    if sf_format not in sf.available_formats():
        raise CodecUnavailable(f"This machine's libsndfile cannot write {fmt.upper()}.")
    return sf


def encode(audio: np.ndarray, fmt: str = "wav", sr: int = SR) -> bytes:
    """`audio` as a file in one of `EXPORT_FORMATS`, in memory.

    "wav" is `wav_bytes`, byte for byte. "flac" and "mp3" go through
    `soundfile` (the `studio` extra: libsndfile, with wheels for x64 and
    ARM64 Windows), imported here and only here — the engine itself never
    needs it. Both encode the same 16-bit samples the WAV holds
    (`_pcm16`), so a FLAC decodes back to exactly the WAV and an MP3 is a
    lossy copy of it, not of something louder. Raises `KeyError` for an
    unknown format and `CodecUnavailable` when the library, or the format
    in the library found, is missing."""
    sf = codec(fmt)
    if sf is None:
        return wav_bytes(audio, sr)
    _media, _ext, sf_format, subtype = EXPORT_FORMATS[fmt]
    pcm = _pcm16(audio)
    extra = {}
    if fmt == "mp3":
        # the same samples as floats: handed int16, libsndfile 1.2.2's MP3
        # writer returns noise (measured: an error at the signal's own level,
        # where the float path's is under 1% of it)
        pcm = pcm.astype(np.float64) / PCM_DECODE_SCALE
        extra = {"bitrate_mode": "VARIABLE", "compression_level": MP3_COMPRESSION}
    buf = io.BytesIO()
    sf.write(buf, pcm, sr, format=sf_format, subtype=subtype, **extra)
    return buf.getvalue()


def read_pcm16(data: bytes) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV bytes -> (mono `(n,)` or stereo `(n, 2)` float64 in
    [-1, 1], sample rate). The one in-memory WAV decoder — it replaces the
    inline decoder `server.py`'s `/clip/upload` route used to hand-roll,
    which divided by `PCM_ENCODE_SCALE` (not `PCM_DECODE_SCALE`); this
    keeps that call site's exact output, and pairs exactly with
    `wav_bytes()` for a lossless (to quantization) round-trip. Raises
    `wave.Error`/`EOFError` on unreadable bytes, `ValueError` on anything
    that isn't 16-bit mono/stereo PCM."""
    with wave.open(io.BytesIO(data)) as w:
        ch, width, sr, n = (
            w.getnchannels(),
            w.getsampwidth(),
            w.getframerate(),
            w.getnframes(),
        )
        raw = w.readframes(n)
    if width != 2 or ch not in (1, 2):
        raise ValueError(f"expected 16-bit PCM mono/stereo, got {ch}ch/{width * 8}-bit")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / PCM_ENCODE_SCALE
    if ch == 2:
        x = x.reshape(-1, 2)
    return x, sr


def save(
    path: str,
    audio: np.ndarray,
    sr: int = SR,
    bit_depth: int = 24,
    normalize_to: float | None = None,
) -> str:
    """Write a WAV file. Returns the path."""
    x = to_stereo(np.asarray(audio, dtype=np.float64))
    if normalize_to:
        x = normalize(x, normalize_to)
    x = np.clip(x, -1.0, 1.0)

    if bit_depth == 16:
        data = (x * PCM_ENCODE_SCALE).astype("<i2").tobytes()
        width = 2
    elif bit_depth == 24:
        ints = (x * 8388607).astype("<i4")
        b = ints.tobytes()
        data = bytearray()
        for i in range(0, len(b), 4):
            data += b[i : i + 3]
        data = bytes(data)
        width = 3
    else:
        data = (x * 2147483647).astype("<i4").tobytes()
        width = 4

    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(width)
        w.setframerate(sr)
        w.writeframes(data)
    return path


def load(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / PCM_DECODE_SCALE
    elif width == 3:
        # save()'s own default depth, which this could not read back: three
        # little-endian bytes a sample, the top one carrying the sign
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (ints - ((ints & 0x800000) << 1)).astype(np.float64) / 8388608
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648
    else:
        raise ValueError("only 16-, 24- and 32-bit WAV loading is supported")
    return x.reshape(-1, ch), sr


def play(audio: np.ndarray, sr: int = SR, blocking: bool = True) -> None:
    """Play through the speakers. Requires `pip install sounddevice`."""
    import sounddevice as sd  # ty: ignore[unresolved-import, unused-ignore-comment]

    x = np.clip(to_stereo(np.asarray(audio, dtype=np.float32)), -1, 1)
    sd.play(x, sr)
    if blocking:
        sd.wait()


def stream(callback, sr: int = SR, blocksize: int = 256, channels: int = 2):
    """
    Run a live audio stream. `callback(n_frames, time_seconds) -> (n, 2) array`.
    Use this to build playable, real-time instruments.

        def cb(n, t):
            return core.sine(220, n)[:, None].repeat(2, 1) * 0.2
        with stream(cb):
            input('playing — press enter to stop')
    """
    import sounddevice as sd  # ty: ignore[unresolved-import, unused-ignore-comment]

    state = {"pos": 0}

    def _cb(outdata, frames, time_info, status):
        t = state["pos"] / sr
        block = np.asarray(callback(frames, t), dtype=np.float32)
        if block.ndim == 1:
            block = np.column_stack([block, block])
        outdata[:] = np.clip(block[:frames], -1, 1)
        state["pos"] += frames

    return sd.OutputStream(
        samplerate=sr,
        blocksize=blocksize,
        channels=channels,
        callback=_cb,
        dtype="float32",
    )


def analyse(audio: np.ndarray, sr: int = SR) -> dict:
    x = to_stereo(np.asarray(audio, dtype=np.float64))
    mono = x.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono**2)))
    peak = float(np.max(np.abs(x)))
    return {
        "seconds": round(len(x) / sr, 2),
        "peak": round(peak, 4),
        "peak_dbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
        "rms_dbfs": round(20 * np.log10(max(rms, 1e-9)), 2),
        "crest_db": round(20 * np.log10(max(peak, 1e-9) / max(rms, 1e-9)), 2),
        "dc_offset": round(float(np.mean(mono)), 6),
        "clipped_samples": int(np.sum(np.abs(x) >= 0.9999)),
        "has_nan": bool(np.any(~np.isfinite(x))),
        "stereo_correlation": round(float(np.corrcoef(x[:, 0], x[:, 1])[0, 1]), 3)
        if np.std(x[:, 0]) > 0 and np.std(x[:, 1]) > 0
        else 1.0,
    }


def spectrum(
    audio: np.ndarray, sr: int = SR, bands: int = 24
) -> list[tuple[float, float]]:
    """Rough octave-band energy — handy for sanity checking a mix."""
    mono = to_stereo(audio).mean(axis=1)
    mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1 / sr)
    edges = np.geomspace(20, sr / 2, bands + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=False):
        m = (freqs >= lo) & (freqs < hi)
        e = float(np.sqrt(np.mean(mag[m] ** 2))) if m.any() else 0.0
        out.append((float(np.sqrt(lo * hi)), 20 * np.log10(max(e, 1e-9))))
    return out
