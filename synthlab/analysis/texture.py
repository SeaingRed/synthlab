"""How a note behaves over time, from one short-time spectrum.

`extract` (features.py) reads the whole note as one FFT. That is right for a
sound's average colour and wrong for anything that changes or that is judged
frame by frame: a whole-note spectral flatness sits near zero for every
pitched sound — the harmonics of a 0.6 s note are needle-thin bins — so
"is this noisy?" had no signal at all, and nothing measured how long a note
rings or whether it moves. The breeder needs all three. `texture` adds them
without touching `Features` (the map, library and meters read that).

Pure NumPy, deterministic: same audio in, same Texture out.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..core import SR

_N = 2048  # ~46 ms frames at 44.1 kHz
_HOP = 1024
_BAND = (200.0, 8000.0)  # where "noisy vs tonal" is judged: above any sub,
# below the air band where every bright patch looks flat
_ACTIVE_DB = -30.0  # frames quieter than this under the loudest are skipped
_DENSITY_DB = -40.0  # a bin within this of the frame's peak counts as present
_EPS = 1e-18


@dataclass(frozen=True)
class Texture:
    env_length: float  # s until the level falls 30 dB under its peak for good
    movement: float  # 0 static .. 1: mean spectral-shape change frame to frame
    flatness: float  # median per-frame flatness over 200 Hz-8 kHz (0 tonal .. 1 noise)
    density: float  # median share of 200 Hz-8 kHz bins within 40 dB of the peak
    loudness_db: float  # the loudest 50 ms, RMS in dBFS
    frame_crest: float  # median peak/RMS (dB) inside 46 ms frames: waveform
    # shape with the envelope taken out — saturation squares it off (sine 3,
    # saw 4.8, square 0; drive pulls a patch down ~1.5-3 dB)
    infra: float  # power share below 30 Hz (rumble nobody hears as a note)

    def to_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def _mono(audio: np.ndarray) -> np.ndarray:
    x = np.asarray(audio, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def _frame_rms(x: np.ndarray, win: int) -> np.ndarray:
    n = len(x) // win
    if n == 0:
        return np.array([float(np.sqrt(np.mean(x**2))) if len(x) else 0.0])
    return np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1))


def envelope_length(audio: np.ndarray, sr: int = SR) -> float:
    """Seconds until a 10 ms RMS envelope last sits within 30 dB of its peak."""
    env = _frame_rms(_mono(audio), max(1, sr // 100))
    peak = float(env.max())
    if peak <= 0.0:
        return 0.0
    above = np.nonzero(env > peak * 10 ** (_ACTIVE_DB / 20))[0]
    return float((above[-1] + 1) * (sr // 100) / sr)


def loudness_db(audio: np.ndarray, sr: int = SR) -> float:
    """The loudest 50 ms window's RMS, in dBFS. A peak can be one click; a
    50 ms window is something you hear, and a slow pad still reaches it."""
    env = _frame_rms(_mono(audio), max(1, sr // 20))
    return float(20 * np.log10(float(env.max()) + 1e-12))


def _stft_power(x: np.ndarray) -> np.ndarray:
    if len(x) < _N:
        x = np.pad(x, (0, _N - len(x)))
    count = 1 + (len(x) - _N) // _HOP
    idx = np.arange(_N)[None, :] + _HOP * np.arange(count)[:, None]
    frames = x[idx] * np.hanning(_N)
    return np.abs(np.fft.rfft(frames, axis=1)) ** 2


def _frame_crest(x: np.ndarray, active: np.ndarray) -> float:
    if not np.any(active):
        return 0.0
    count = len(active)
    idx = np.arange(_N)[None, :] + _HOP * np.nonzero(active)[0][:, None]
    frames = np.pad(x, (0, max(0, _N + _HOP * (count - 1) - len(x))))[idx]
    rms = np.sqrt(np.mean(frames**2, axis=1)) + 1e-12
    peak = np.max(np.abs(frames), axis=1) + 1e-12
    return float(np.median(20 * np.log10(peak / rms)))


def texture(audio: np.ndarray, sr: int = SR) -> Texture:
    x = _mono(audio)
    power = _stft_power(x)
    freqs = np.fft.rfftfreq(_N, 1 / sr)
    band = (freqs >= _BAND[0]) & (freqs <= _BAND[1])

    total = float(power.sum()) + _EPS
    infra = float(power[:, freqs < 30.0].sum()) / total

    frame_e = power.sum(axis=1)
    active = frame_e > frame_e.max() * 10 ** (_ACTIVE_DB / 10)
    if total <= _EPS:  # silence: nothing is active
        active[:] = False
    bp = power[active][:, band] + _EPS
    if len(bp):
        flat = np.exp(np.mean(np.log(bp), axis=1)) / np.mean(bp, axis=1)
        present = bp > bp.max(axis=1, keepdims=True) * 10 ** (_DENSITY_DB / 10)
        flatness = float(np.median(flat))
        density = float(np.median(present.mean(axis=1)))
    else:
        flatness = density = 0.0

    # movement: how much the *shape* of the spectrum changes between
    # neighbouring frames, level taken out (L1-normalised magnitudes), so a
    # filter sweep, PWM, a wavetable scan or an LFO counts and a plain decay
    # does not. Half the L1 distance of two distributions: 0 same, 1 disjoint.
    mags = np.sqrt(power[active])
    if len(mags) >= 2:
        shape = mags / (mags.sum(axis=1, keepdims=True) + _EPS)
        movement = float(np.mean(0.5 * np.abs(np.diff(shape, axis=0)).sum(axis=1)))
    else:
        movement = 0.0

    return Texture(
        env_length=envelope_length(x, sr),
        frame_crest=_frame_crest(x, active),
        movement=movement,
        flatness=flatness,
        density=density,
        loudness_db=loudness_db(x, sr),
        infra=infra,
    )
