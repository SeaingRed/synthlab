"""The spectral oscillator (shape "spectral"): the sampler's sample
resynthesised from its spectrum over time, with time and pitch apart.

What it reads is the sampler's, as the granular shape's is:
`sampler_sample` (the audio by content, `sampler.get`), its span
(`sampler_start` / `sampler_end`) — or, with `sampler_loop` "on", the loop
inside it — and its pitch (`sampler_root`, `sampler_keytrack`: the played
key's ratio to the root, as the sampler's tape read takes it).

**The analysis** (`analyse`): a short-time Fourier transform of the whole
sample — a 2048-point periodic Hann window every 512 samples (`N`, `HA`),
each frame centred on its sample and its phase referred to that centre —
one per channel (one alone when the two are the same), and a spectral-flux
onset strength per frame. It depends on the sample alone, so it is
computed once per (sample id, `ANALYSIS`) and kept in a byte-bounded
memory cache (`CACHE_BYTES`, least recently used out): 42 MB for a 30 s
stereo sample. Never on disk: it is rebuilt in well under a second.

**The resynthesis** (`render`) is a phase vocoder with identity phase
locking and peak shifting (Laroche & Dolson): an output frame every `HS`
(256) samples reads the analysis at a fractional frame — magnitudes
interpolated between the two frames either side, phases from the nearer —
and

- picks the spectral peaks (a bin above its two neighbours each side) of
  the channels' summed magnitude; every bin belongs to its nearest peak's
  region;
- measures each peak's true frequency from its phase advance between the
  two frames (the vocoder's instantaneous frequency), multiplies it by the
  pitch ratio and moves the peak's whole region by the whole bins nearest
  that (`Δ`); the peak's output phase advances by the shifted frequency x
  `HS` from where the last frame left that bin, and every bin of the region
  keeps its phase relative to the peak (identity locking — what keeps a
  partial's lobe one sinusoid and the vocoder from its worst phasiness);
  the same rotation goes on every channel, so a stereo image keeps its
  interchannel phase;
- weights each region by the partial cut (its peak's number above the
  root, `spectral_low` / `spectral_high`, a one-partial taper) and the
  timbre shift (`envelope_gain`: the frame's envelope traced through its
  peaks, read where the shift puts the formants over where the peak sits,
  held to ±40 dB) — `spectral_timbre` semitones moves the envelope over
  where the pitch put it ("move": at 0 a plain transposition, the formants
  riding the pitch) or over where the recording has it ("keep": at 0 the
  formants stay as the key moves);
- overlap-adds the frames under the same window (normalised by its sum of
  squares).

Where the read sits is `spectral_pos` (0..1 of the region; a curve or a
route moves it, read at each frame) plus `spectral_scan` x the seconds
since note-on, in the recording's seconds (1 plays at its own speed, 0.5
twice as long at the same pitch, 0 freezes, negative backwards). With the
loop on the read wraps round the loop; off, it holds at the span's ends —
a freeze of the last (or first) frame. The frames that end before the
note's first sample read the recording just before where the note starts
(the pre-roll), so a note starting at the recording's own start sounds as
it does.

The pitch ratio is taken once a frame: the mean of the key's ratio to the
root over the frame's hop (glide, vibrato, bends, pitch routes all reach it,
5.8 ms apart). The frequency of a partial is its phase advance — exact —
while its place in the frame is the nearest whole bin.

`spectral_transient` > 0 keeps attacks sharp where the read is slower
than the recording (`transient_reads`): round each onset the read crosses
going forward — found by spectral flux, placed to the sample by the
signal's own rise (`Analysis.attack`) — the frames reading the attack
(`ATTACK` frames a side) are read at the recording's speed, landing the
attack when the slow read would have reached it; the slow reads that
would have fallen there hold the window's edges instead (the stretch goes
into what comes before and after an attack), and the frame on the attack
takes the analysis's own phases (re-locked: no phases accumulated through
the frames before it). The amount is a sensitivity: onsets whose strength
(over the sample's strongest) reaches 1 - 0.9 x amount. At the
recording's speed or faster it changes nothing.

Nothing here draws a random number: the first frame takes the analysis's
own phases. Stereo out (n, 2) — the sample's two channels, the same when
the sample is mono — or zeros (n,) with nothing to play (no sample, a
region too short), as the sampler and the granular shape give.

Not live: the keys play a spectral sound from the server's WAV (the
studio's `paramui` classifies every `spectral_*` field "default").
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

from . import sampler
from .core import SR, as_array

N = 2048  # the window, in samples
HA = 512  # analysis hop
HS = 256  # synthesis hop
BINS = N // 2 + 1
PRE = N // (2 * HA) - 1  # analysis frames centred before the sample's start
HEAD = PRE + N // (2 * HA)  # the first analysis frame the start leaves whole
ANALYSIS = (N, HA, 1)  # the settings an analysis is keyed by (+ its version)
CACHE_BYTES = 160 << 20

SCAN = 4.0  # the recording's speed either way, at most
TIMBRE = 24.0  # semitones either way
LOW_MAX = 32.0  # the low cut, in partials of the root
HIGH = (1.0, 512.0)  # the high cut; at its top nothing is cut
FORMANTS = ("move", "keep")
ENV_GAIN = 100.0  # the timbre shift's gain, held to ±40 dB
MIN_LOOP = 64  # a loop shorter than this is no region (the sampler's rule)
ONSET_WIDE = 3  # an onset is the flux's maximum within this many frames
ONSET_FLOOR = 0.1  # the weakest onset the transient amount ever takes
ATTACK = N // (2 * HA)  # frames a side of an onset read at the recording's speed

BLOCK = 256  # output frames synthesised and overlap-added at a time

_WIN = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(N) / N)  # periodic Hann
_OLA = 3.0 * N / (8.0 * HS)  # the window's squares summed at HS
_BIN_RAD = 2.0 * np.pi / N
_K = np.arange(BINS)


@dataclass(frozen=True)
class Settings:
    """The Patch fields the spectral shape reads, as `Synth._osc_stack`
    hands them over. `pos` may be a per-sample array (a curve, a route)."""

    sample: str = ""
    root: int = 60
    start: float = 0.0
    end: float = 1.0
    loop: str = "off"
    loop_start: float = 0.0
    loop_end: float = 1.0
    keytrack: str = "on"
    pos: object = 0.0
    scan: float = 1.0
    timbre: float = 0.0
    formants: str = "move"
    low: float = 0.0
    high: float = HIGH[1]
    transient: float = 0.0

    @classmethod
    def of(cls, p) -> Settings:
        return cls(
            p.sampler_sample,
            p.sampler_root,
            p.sampler_start,
            p.sampler_end,
            p.sampler_loop,
            p.sampler_loop_start,
            p.sampler_loop_end,
            p.sampler_keytrack,
            p.spectral_pos,
            p.spectral_scan,
            p.spectral_timbre,
            p.spectral_formants,
            p.spectral_low,
            p.spectral_high,
            p.spectral_transient,
        )


def _scalar(v, default: float, lo: float, hi: float) -> float:
    return min(max(sampler._first(v, default), lo), hi)


def region(L: int, st: Settings) -> tuple[int, int]:
    """The source samples the read lives in, [r0, r1): the span, or the
    loop inside it when the loop is on and at least MIN_LOOP long (the
    granular shape's rule)."""
    return region_of(L, st)[:2]


def region_of(L: int, st: Settings) -> tuple[int, int, bool]:
    """The region, and whether it is the loop (the read wraps round it)."""
    a = _scalar(st.start, 0.0, 0.0, 1.0)
    b = _scalar(st.end, 1.0, 0.0, 1.0)
    r0, r1 = int(a * L), int(b * L)
    if st.loop == "on":
        lo = int(_scalar(st.loop_start, 0.0, 0.0, 1.0) * L)
        hi = int(_scalar(st.loop_end, 1.0, 0.0, 1.0) * L)
        lo, hi = max(lo, r0), min(hi, r1)
        if hi - lo >= MIN_LOOP:
            return lo, hi, True
    return r0, r1, False


# --- the analysis ---------------------------------------------------------------


@dataclass(frozen=True)
class Analysis:
    """A sample's STFT, (channels, frames, BINS) complex64 — frame i
    centred on source sample (i - PRE) x HA, its phase referred to that
    centre — each frame's onset strength, 0..1 of the loudest, and where an
    onset frame's attack sits, in frames (the frame's own index elsewhere)."""

    spectra: np.ndarray
    onset: np.ndarray
    attack: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.spectra.nbytes + self.onset.nbytes + self.attack.nbytes)

    @property
    def frames(self) -> int:
        return int(self.spectra.shape[1])


def frame_count(L: int) -> int:
    """Analysis frames of a sample `L` long: centred from -PRE x HA to past
    its last sample."""
    return L // HA + PRE + 2


def _stft(x: np.ndarray, out: np.ndarray) -> None:
    """One channel's (frames, BINS) complex64 into `out`, in chunks (the
    frame matrix of a 30 s sample at once would be 42 MB of float64 for
    nothing)."""
    L = len(x)
    frames = frame_count(L)
    head = N // 2 + PRE * HA
    xp = np.zeros(head + L + N + 2 * HA)
    xp[head : head + L] = x
    view = np.lib.stride_tricks.sliding_window_view(xp, N)[::HA][:frames]
    for a in range(0, frames, 256):
        block = view[a : a + 256] * _WIN
        # the centre to index 0: each frame's phase is referred to its centre
        out[a : a + 256] = np.fft.rfft(np.roll(block, -N // 2, axis=1), axis=1)


def _onsets(spectra: np.ndarray) -> np.ndarray:
    """Each frame's onset strength: the half-wave rectified rise of its
    compressed magnitudes over the frame before (spectral flux), kept where
    it is the largest within ONSET_WIDE frames and over the median round it,
    as a share of the sample's largest; 0 elsewhere."""
    mag = np.abs(spectra[0])
    for c in range(1, len(spectra)):
        mag += np.abs(spectra[c])
    comp = np.log1p(100.0 * mag / max(float(mag.max()), 1e-12))
    flux = np.zeros(len(comp))
    flux[1:] = np.maximum(comp[1:] - comp[:-1], 0.0).sum(axis=1)
    if len(flux) < 3 or flux.max() <= 0:
        return np.zeros(len(flux), dtype=np.float32)
    w = 2 * ONSET_WIDE + 1
    padded = np.pad(flux, ONSET_WIDE, mode="edge")
    wins = np.lib.stride_tricks.sliding_window_view(padded, w)
    peak = flux >= wins.max(axis=1)
    padded = np.pad(flux, 8, mode="edge")
    med = np.median(np.lib.stride_tricks.sliding_window_view(padded, 17), axis=1)
    strength = np.where(peak, np.maximum(flux - med, 0.0), 0.0)
    top = strength.max()
    return (strength / top if top > 0 else strength).astype(np.float32)


def _attacks(x: np.ndarray, onset: np.ndarray) -> np.ndarray:
    """Where each onset frame's attack sits, to the sample, in frames: the
    first point from half a hop before the frame's centre to half a hop
    past its window's end (the flux rises as an attack enters a window, so
    up to half a window before the attack) where the signal's 64-sample RMS
    has risen half of its largest rise there over its lowest before it. A
    frame with no onset keeps its index."""
    at = np.arange(len(onset), dtype=np.float64)
    w = 64
    for i in np.flatnonzero(onset > 0):
        c = (i - PRE) * HA
        a, b = max(c - HA, 0), min(c + N // 2 + HA + w, len(x))
        if b - a <= 2 * w:
            continue
        e = np.sqrt(np.convolve(x[a:b] ** 2, np.ones(w) / w, mode="valid"))
        rise = e - np.minimum.accumulate(e)
        if rise.max() <= 0:
            continue
        k = int(np.argmax(rise >= 0.5 * rise.max()))
        at[i] = (a + k + w - 1) / HA + PRE
    return at


class _Store:
    """(sample id, ANALYSIS) -> Analysis, least recently used out past
    `max_bytes`; one analysis computed at a time (renders run in threads)."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: dict[tuple, Analysis] = {}
        self._lock = threading.Lock()
        self._work = threading.Lock()

    def _get(self, key: tuple) -> Analysis | None:
        with self._lock:
            a = self._d.pop(key, None)
            if a is not None:
                self._d[key] = a  # most recent last
            return a

    def get(self, sid: str) -> Analysis | None:
        x = sampler.get(sid)
        if x is None or len(x) < 4:
            return None
        key = (sid, ANALYSIS)
        a = self._get(key)
        if a is not None:
            return a
        with self._work:
            a = self._get(key)
            if a is not None:
                return a
            a = analyse(x)
            with self._lock:
                self._d[key] = a
                total = sum(v.nbytes for v in self._d.values())
                while total > self.max_bytes and len(self._d) > 1:
                    old = next(iter(self._d))
                    total -= self._d.pop(old).nbytes
            return a

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def analyse(x: np.ndarray) -> Analysis:
    """The analysis of stereo audio (L, 2): one channel when both are the
    same sample for sample."""
    x = np.asarray(x)
    if x.ndim == 1:
        x = x[:, None]
    chans = [x[:, 0]] if np.array_equal(x[:, 0], x[:, -1]) else [x[:, 0], x[:, 1]]
    spectra = np.empty((len(chans), frame_count(len(x)), BINS), dtype=np.complex64)
    for c, ch in enumerate(chans):
        _stft(np.asarray(ch, dtype=np.float64), spectra[c])
    onset = _onsets(spectra)
    return Analysis(spectra, onset, _attacks(x.sum(axis=1), onset))


STORE = _Store(CACHE_BYTES)


def analysis(sid) -> Analysis | None:
    """The sample's analysis (computed once, then from the cache), or None
    when this machine holds no such sample."""
    return STORE.get(sid) if sampler.valid_id(sid) else None


# --- the resynthesis ----------------------------------------------------------


def _smooth(u: np.ndarray) -> np.ndarray:
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def cut_gain(h: np.ndarray, low: float, high: float) -> np.ndarray | None:
    """The partial cut's weight at partial number `h` (a peak's frequency
    over the root's): 0 below partial `low` - 1, 1 from `low`, 1 to `high`,
    0 from `high` + 1, smoothstepped between. None when nothing is cut
    (low 0, high at its top)."""
    g = None
    if low > 0:
        g = _smooth(h - (low - 1.0))
    if high < HIGH[1]:
        top = _smooth(high + 1.0 - h)
        g = top if g is None else g * top
    return g


def envelope_gain(true: np.ndarray, level: np.ndarray, where: np.ndarray):
    """Each peak's gain under the timbre shift: the frame's spectral
    envelope — its peaks' levels joined by straight lines in log level over
    their true frequencies, held flat past the first and the last — read
    at `where` over the peak's own level, held to ±40 dB. Through the peaks
    rather than a smoothed spectrum: a harmonic sound's envelope is what
    its harmonics trace (a cepstral one sinks into the valleys between
    them and blurs a formant narrower than its lifter)."""
    log = np.log(np.maximum(level, 1e-30))
    there = np.interp(where, true, log)
    return np.clip(np.exp(there - log), 1.0 / ENV_GAIN, ENV_GAIN)


def _princarg(a: np.ndarray) -> np.ndarray:
    return a - 2.0 * np.pi * np.round(a / (2.0 * np.pi))


def frame_centres(n: int) -> np.ndarray:
    """The output frames' centres: every HS samples whose window reaches
    into [0, n)."""
    j0 = -(N // 2) // HS + 1
    j1 = -(-(n + N // 2) // HS)
    return np.arange(j0, j1, dtype=np.int64) * HS


def read_positions(
    centres: np.ndarray, n: int, st: Settings, L: int, sr: int = SR
) -> np.ndarray:
    """Where each output frame reads, in source samples: the position plus
    the scan, wrapped round the loop or held at the span's ends; a frame
    before the note's first sample reads back from the first read by the
    scan (the pre-roll)."""
    r0, r1, looped = region_of(L, st)
    span = r1 - r0
    q = SR / sr
    scan = _scalar(st.scan, 1.0, -SCAN, SCAN)
    pos = np.asarray(st.pos, dtype=np.float64)
    top = math.nextafter(1.0, 0.0)
    at = np.clip(centres, 0, max(n - 1, 0))
    if pos.ndim:
        p = np.clip(np.nan_to_num(as_array(pos, max(n, 1))[at]), 0.0, top)
    else:
        p = np.full(len(centres), min(max(float(np.nan_to_num(pos)), 0.0), top))
    s = p * span + scan * np.maximum(centres, 0) * q
    if looped:
        s = np.mod(s, span)
    else:
        s = np.clip(s, 0.0, span)
    s = s + scan * np.minimum(centres, 0) * q
    return r0 + s


def _ratios(rel: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """The pitch ratio each frame plays: the mean over its hop round its
    centre (its ends held past the note's)."""
    n = len(rel)
    cs = np.concatenate([[0.0], np.cumsum(rel)])
    a = np.clip(centres - HS // 2, 0, n - 1)
    b = np.clip(centres + HS // 2, 1, n)
    b = np.maximum(b, a + 1)
    return (cs[b] - cs[a]) / (b - a)


def render(freq, n: int, sr: int = SR, settings: Settings | None = None, key_hz=None):
    """The spectral oscillator: stereo (n, 2), or zeros (n,) when there is
    nothing to play."""
    st = settings or Settings()
    if n <= 0:
        return np.zeros(max(n, 0))
    an = analysis(st.sample)
    if an is None:
        return np.zeros(n)
    x = sampler.get(st.sample)
    L = len(x)
    r0, r1 = region(L, st)
    if r1 - r0 < 4:
        return np.zeros(n)
    f = np.asarray(as_array(freq, n), dtype=np.float64)
    key = max(float(key_hz) if key_hz else float(f[0]), 1e-3)
    root_hz = sampler.midi_hz(sampler._first(st.root, 60.0))
    rel = f / root_hz if st.keytrack != "off" else f / key

    centres = frame_centres(n)
    ratio = _ratios(rel, centres)  # the musical pitch ratio, a frame each
    frames = read_positions(centres, n, st, L, sr) / HA + PRE
    C = an.spectra.shape[0]
    buf = np.zeros((C, n + 2 * N))
    blocks = _synthesise(an, frames, ratio * (SR / sr), ratio, root_hz, st, SR / sr)
    for j0, y in blocks:
        _overlap_add(buf, y, centres[j0 : j0 + y.shape[1]])
    out = buf[:, N : N + n] / _OLA
    if C == 1:
        out = np.vstack([out, out])
    return np.ascontiguousarray(out.T)


def _synthesise(
    an: Analysis,
    frames: np.ndarray,
    shift: np.ndarray,
    ratio: np.ndarray,
    root_hz: float,
    st: Settings,
    q: float = 1.0,
):
    """Each output frame's spectrum, BLOCK frames at a time: (first frame,
    (channels, frames, BINS) complex). `q` is source samples an output
    sample (SR / sr)."""
    X = an.spectra
    C, F, _ = X.shape
    J = len(frames)
    out = np.zeros((C, min(BLOCK, J), BINS), dtype=np.complex128)
    low = _scalar(st.low, 0.0, 0.0, LOW_MAX)
    high = _scalar(st.high, HIGH[1], *HIGH)
    t = 2.0 ** (_scalar(st.timbre, 0.0, -TIMBRE, TIMBRE) / 12.0)
    keep = st.formants == "keep"
    hz_per_bin = SR / N
    step = HS * q / HA  # the read's advance a frame at the recording's speed
    amount = _scalar(st.transient, 0.0, 0.0, 1.0)
    hit = np.zeros(J, dtype=bool)
    if amount > 0:
        strong = an.onset >= 1.0 - (1.0 - ONSET_FLOOR) * amount
        frames, hit = transient_reads(frames, an.attack[strong], step)

    theta = np.zeros(BINS)  # the last output frame's phase, a bin each
    advance = _BIN_RAD * HS * _K  # a bin's own phase advance at its centre
    prev = -np.inf
    opening = True
    for j in range(J):
        if j and j % BLOCK == 0:
            yield j - BLOCK, out
            out = np.zeros((C, min(BLOCK, J - j), BINS), dtype=np.complex128)
        here = float(frames[j])  # may lie past either end: held there
        # the opening: the first frame takes the analysis's own phases, and
        # while the read moves as recorded through the frames the sample's
        # start cuts short (their phases are no partial's), so do the next —
        # a sample played at its root from its start opens as recorded
        at_root = abs(shift[j] - 1.0) < 1e-9
        recorded = abs(here - prev - step) < 1e-9 and at_root
        opening = j == 0 or (opening and recorded and here < HEAD)
        # (and so does the frame on an attack, re-locked)
        reset = opening or hit[j]
        prev = here
        a = min(max(here, 0.0), F - 1.0)
        i0 = min(int(a), F - 1)
        fr = a - i0
        i1 = min(i0 + 1, F - 1)
        near = i1 if fr >= 0.5 else i0
        mags = np.abs(X[:, i0]) * (1.0 - fr) + np.abs(X[:, i1]) * fr
        phases = np.angle(X[:, near])
        m = mags.sum(axis=0)
        peaks = _peaks(m)
        if not len(peaks):
            theta = theta + advance
            continue
        # each peak's true frequency, in bins: its phase advance from the
        # frame before to the frame after where the read sits
        refn = _reference(X[:, near])
        true = _true_bins(X, i0, i1, peaks)
        target = true * shift[j]
        delta = np.round(target - peaks).astype(np.int64)
        moved, src, own = _mapping(peaks, delta)
        # the region's weight: the partial cut, the timbre shift
        g = np.ones(len(peaks))
        h = true * hz_per_bin / root_hz
        cut = cut_gain(h, low, high)
        if cut is not None:
            g = g * cut
        where = true * ratio[j] / t if keep else true / t
        if np.any(np.abs(where - true) > 1e-9):
            g = g * envelope_gain(true, m[peaks], where)
        # the peak's phase: on from where the last frame left its new bin,
        # by the shifted frequency (or the analysis's own on a reset)
        phi_ref = np.angle(refn[peaks])
        if reset:
            # the analysis's own phases, carried from the frame they were
            # read in to where the read sits (by the frequency read off the
            # nearer frame and the one past it: the one before may be cut
            # short by the sample's start)
            carry = _true_bins(X, near, near + 1, peaks)
            rot = _BIN_RAD * carry * (here - near) * HA
        else:
            rot = theta[np.clip(moved, 0, BINS - 1)] + _BIN_RAD * HS * target - phi_ref
        theta = theta + advance
        d = np.flatnonzero(own >= 0)
        k, o = src[d], own[d]
        rk, gk = rot[o], g[o]
        theta[d] = np.angle(refn[k]) + rk
        for c in range(C):
            out[c, j % BLOCK, d] = mags[c, k] * gk * np.exp(1j * (phases[c, k] + rk))
    if J:
        yield (J - 1) // BLOCK * BLOCK, out


def _mapping(peaks: np.ndarray, delta: np.ndarray):
    """Where each peak's region goes: the moved peaks' bins, and for every
    output bin the source bin it is filled from and the peak that owns it
    (-1: none). An output bin belongs to the nearest moved peak and is
    filled from that peak's own source region alone, so regions never
    overlap — moved down they are trimmed to the narrower spacing, moved up
    the gaps between them stay empty."""
    moved = peaks + delta
    order = np.argsort(moved, kind="stable")
    ms = moved[order]
    bounds = (ms[:-1] + ms[1:] + 1) // 2
    own = order[np.searchsorted(bounds, _K, side="right")]
    src = _K - delta[own]
    inside = (src >= 0) & (src < BINS)
    src_c = np.clip(src, 0, BINS - 1)
    # the source bin's own peak (the regions there split at midpoints)
    sb = (peaks[:-1] + peaks[1:] + 1) // 2
    src_own = np.searchsorted(sb, src_c, side="right")
    own = np.where(inside & (src_own == own), own, -1)
    return moved, src_c, own


def transient_reads(frames: np.ndarray, attacks: np.ndarray, step: float):
    """The reads re-timed round each attack the read crosses forward slower
    than the recording: ATTACK frames either side of it are read at the
    recording's speed (`step` a frame), placed so that the attack lands
    when the slow read would have reached it; the slow reads that fall
    inside that window hold at its edges instead (a held frame: the
    stretch goes into what comes before and after the attack, not into the
    attack). Returns the reads and, marked, the frame nearest each attack."""
    J = len(frames)
    out = frames.astype(np.float64).copy()
    lock = np.zeros(J, dtype=bool)
    hit = np.zeros(J, dtype=bool)
    k = ATTACK / step
    for o in np.asarray(attacks, dtype=np.float64):
        cross = np.flatnonzero((frames[:-1] < o) & (frames[1:] >= o)) + 1
        for jo in cross:
            v = frames[jo] - frames[jo - 1]
            if not 0 < v < step - 1e-9:
                continue
            at = jo - 1 + (o - frames[jo - 1]) / v  # when the slow read is there
            lo, hi = max(int(math.ceil(at - k)), 0), min(int(at + k), J - 1)
            out[lo : hi + 1] = o + (np.arange(lo, hi + 1) - at) * step
            lock[lo : hi + 1] = True
            hit[min(max(int(round(at)), lo), hi)] = True
            j = lo - 1  # the slow reads before, inside the window: held
            while j >= 0 and out[lo] < frames[j] <= o and not lock[j]:
                out[j] = out[lo]
                j -= 1
            j = hi + 1  # and after, until the slow read has passed it
            while j < J and o <= frames[j] < out[hi] and not lock[j]:
                out[j] = out[hi]
                j += 1
    return out, hit


def _true_bins(X: np.ndarray, a: int, b: int, peaks: np.ndarray) -> np.ndarray:
    """The peaks' true frequencies, in bins: the phase advance from frame
    `a` to frame `b` (the pair one back where `b` is past the last)."""
    F = X.shape[1]
    if b > F - 1:
        a, b = F - 2, F - 1
    if a < 0 or b <= a:
        return peaks.astype(np.float64)
    dphi = np.angle(_reference(X[:, b])[peaks]) - np.angle(_reference(X[:, a])[peaks])
    dev = _princarg(dphi - _BIN_RAD * HA * peaks) / (_BIN_RAD * HA)
    return peaks + dev


def _reference(Xi: np.ndarray) -> np.ndarray:
    """The frame the phases are read from: the channels summed, or the
    first channel where they cancel."""
    if len(Xi) == 1:
        return Xi[0]
    s = Xi.sum(axis=0)
    weak = np.abs(s) < 1e-3 * np.abs(Xi).sum(axis=0)
    return np.where(weak, Xi[0], s)


def _peaks(m: np.ndarray) -> np.ndarray:
    """Bins above both neighbours each side and above -100 dB of the
    frame's loudest."""
    floor = max(float(m.max()) * 1e-5, 1e-9)
    c = m[2:-2]
    is_peak = (c > m[1:-3]) & (c >= m[3:-1]) & (c > m[:-4]) & (c >= m[4:]) & (c > floor)
    return np.flatnonzero(is_peak) + 2


def _overlap_add(buf: np.ndarray, y: np.ndarray, centres: np.ndarray) -> None:
    """Frames' spectra back to time, windowed and added into `buf` (output
    sample i at buf[:, i + N])."""
    frames = np.fft.irfft(y, n=N, axis=2)
    frames = np.roll(frames, N // 2, axis=2) * _WIN
    for k, c in enumerate(centres):
        at = int(c) + N // 2
        buf[:, at : at + N] += frames[:, k]
