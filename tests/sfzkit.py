"""Test instruments for the SFZ import: tones synthesised here, packed into
zips here (no audio file ships). `zip_of` writes names exactly as given,
even the hostile ones `zipfile.ZipInfo` would otherwise tidy."""

from __future__ import annotations

import io
import zipfile

import numpy as np
import soundfile as sf

from synthlab.audio import wav_bytes
from synthlab.core import SR


def tone(hz: float, seconds: float = 0.3, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return np.sin(2 * np.pi * hz * t) * amp


def wav(hz: float, seconds: float = 0.3, stereo: bool = True, sr: int = SR) -> bytes:
    x = tone(hz, seconds, sr=sr)
    return wav_bytes(np.column_stack([x, x]) if stereo else x, sr)


def encoded(hz: float, fmt: str, subtype: str | None = None, sr: int = SR) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, tone(hz, sr=sr), sr, format=fmt, subtype=subtype)
    return buf.getvalue()


def zip_of(files: dict[str, bytes | str], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, body in files.items():
            info = zipfile.ZipInfo("x")
            info.filename = name  # past the constructor's separator tidying
            info.compress_type = method
            z.writestr(info, body.encode() if isinstance(body, str) else body)
    return buf.getvalue()


def small_instrument() -> bytes:
    """Three tones on three key spans, a velocity split on the middle
    one and a loop on the top: the smallest thing worth importing."""
    sfz = """
<control> default_path=Samples/
<group> ampeg_attack=0.01 ampeg_release=0.4
<region> sample=low.wav lokey=c2 hikey=b2 pitch_keycenter=a2
<region> sample=mid.wav lokey=c3 hikey=b3 pitch_keycenter=a3 hivel=80
<region> sample=mid.wav lokey=c3 hikey=b3 pitch_keycenter=a3 lovel=81 volume=-3
<region> sample=high.wav lokey=c4 hikey=b4 pitch_keycenter=a4
  loop_mode=loop_continuous loop_start=1000 loop_end=9000
"""
    return zip_of(
        {
            "Inst/inst.sfz": sfz,
            "Inst/Samples/low.wav": wav(110),
            "Inst/Samples/mid.wav": wav(220),
            "Inst/Samples/high.wav": wav(440, stereo=False),
        }
    )
