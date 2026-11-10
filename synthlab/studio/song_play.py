"""Playing and exporting the song: the pure pieces behind
`/song/plan.json`, `/song/section/{id}.wav`, `/export/song.wav` and
`/export/stems.zip`. `StudioService` (`song_plan`, `section_result`,
`song_mixdown`, `song_stems`) wires them to the caches.

The song is ONE stitching (`StudioService._song_raw`) of section renders
(`render.section_master_audio`: stems, then the master chain, per section),
in `song_model.section_timeline` order: each section's first pass has the
previous section's (master-processed) tail folded into its head, every
later pass its own tail. The browser queues exactly those WAVs; the export
writes exactly those arrays, so export == playback sample for sample. The
master chain's per-section caveats are listed on `section_master_audio`.

**Gain** is one number for the whole song (`gain_of`), from the loudest
of those passes, so sections never jump in level against each other. A
plain scale, no limiter: a limiter's gain envelope depends on what came
before it, which a section WAV can't know. The scale is capped so no pass
peaks over `CEILING`, which also keeps `wav_bytes` from rescaling any WAV
on its own.

**Loudness targets** (`LOUDNESS_TARGETS`, `master`) are the export's alone:
the finished mixdown, as one piece, brought to a programme loudness and
limited as a one-shot. Playback, the plan's WAVs and the stems never see
it, and the default target ("peak") is the mixdown untouched.
"""

from __future__ import annotations

import copy
import math

import numpy as np
from scipy.signal import lfilter, lfilter_zi

from ..analysis import integrated_loudness, peak_db, true_peak_db
from ..core import SR, n_samples, scan
from . import limits
from . import song_model as sm
from .render import RenderError

TARGET = 0.89  # normalize_master's: the body of the mix lands here
CEILING = 0.97  # and no sample of any cycle goes past this


def check_length(song: dict) -> None:
    """Refuse a song over `MAX_SONG_SECONDS`, or holding a section whose
    cycle is over `MAX_SECTION_SECONDS`, from the song's numbers alone —
    before anything renders (a 64-bar section at 40 bpm is a 384 s cycle).
    Raises RenderError, which the routes answer with a 422 toast."""
    total = sm.song_seconds(song)
    if total > limits.MAX_SONG_SECONDS:
        raise RenderError(
            f"The song is {total:.0f}s — the cap is "
            f"{limits.MAX_SONG_SECONDS:.0f}s. Trim some repeats or bars."
        )
    bar = 4 * 60.0 / song["bpm"]
    for sec in song["sections"]:
        cycle = sm.cycle_bars(song, sec) * bar
        if sm.timed(song, sec):  # its own bar and tempo
            cycle = sm.section_clock(song, sec).seconds(sm.cycle_bars(song, sec))
        if cycle > limits.MAX_SECTION_SECONDS:
            # a take, an automation lane, a trimmed block or a ramp plays
            # the section as one pass
            whole = (
                sec.get("audio")
                or sec.get("spans")
                or sec.get("ramp")
                or sm.section_lanes(song, sec)
            )
            why = (
                "a trimmed block plays it as one pass"
                if sec.get("spans")
                else "its tempo ramp plays it as one pass"
                if sec.get("ramp")
                else "its take or automation plays it as one pass"
            )
            raise RenderError(
                f"Section “{sec['name']}” renders a {cycle:.0f}s cycle — the "
                f"cap is {limits.MAX_SECTION_SECONDS:.0f}s. Raise the bpm, or "
                + (
                    f"shorten the section ({why})."
                    if whole
                    else "use patterns whose lengths divide the section's bars."
                )
            )


def levels(x: np.ndarray) -> tuple[float, float]:
    """(peak, reference) of a cycle: its loudest sample and the 99.5th
    percentile of its frames' peaks `normalize_master` uses as the body of
    the mix — one `core.scan` (the percentile exactly NumPy's). NaN for
    both when a sample is not finite."""
    if x.size == 0:
        return 0.0, 0.0
    s = scan(x, 99.5)
    if not s.finite:
        return math.nan, math.nan
    return s.peak, s.body


def gain_of(all_levels) -> float:
    """The song-wide gain: the loudest cycle's body to TARGET, capped so no
    cycle's peak passes CEILING. A silent song is left at 1."""
    all_levels = list(all_levels)
    peak = max((p for p, _r in all_levels), default=0.0)
    ref = max((r for _p, r in all_levels), default=0.0) or peak
    if ref <= 1e-9:
        return 1.0
    return float(min(TARGET / ref, CEILING / peak))


# --- export loudness -----------------------------------------------------------

# What `/export/song.wav?loudness=` takes (the whitelist) and the Song page
# offers. "peak" is the export as it always was — the playback plan sample
# for sample, levelled by `gain_of` — and stays the default; the others
# master the mixdown to an integrated loudness (LUFS, ITU-R BS.1770):
# -14 is where the streaming services play everything back, -9 a loud
# club master.
LOUDNESS_TARGETS: dict[str, dict] = {
    "peak": {"label": "as played", "lufs": None},
    "streaming": {"label": "-14 LUFS (streaming)", "lufs": -14.0},
    "club": {"label": "-9 LUFS (loud club)", "lufs": -9.0},
}
# A mastered export's ceiling, -1 dBTP: the headroom a lossy encoder needs
# (the services ask for it), where the loop's 0.97 leaves a quarter of a dB.
MASTER_CEILING = 10 ** (-1 / 20)
MASTER_PASSES = 3  # gain corrections before `master` settles for what it has
MASTER_TOLERANCE = 0.1  # LU: close enough to the target to stop


def master(audio: np.ndarray, lufs: float | None) -> np.ndarray:
    """The mixdown at `lufs` integrated loudness, peaks under
    `MASTER_CEILING`: one gain from its measured loudness to the target,
    the lookahead limiter as a one-shot (`loop=False`: nothing lies ahead
    of a song's last block), and — the limiter reads samples, and the
    waveform between them can still overshoot — a plain trim when the true
    peak is over the ceiling. None (the "peak" target) and a song with no
    loudness to measure (silence) come back untouched.

    The limiter and the trim cost level (measured: 0.5 LU on a mix with
    loud transients), so the gain is corrected by the shortfall and the
    chain run again, `MASTER_PASSES` times at most, always from the
    original audio: one limiter over one gain, never a limiter over a
    limited signal. A mix that can't be that loud under the ceiling stays
    under the target, and `loudness_report` says where it landed."""
    if lufs is None:
        return audio
    measured = integrated_loudness(audio)
    if not math.isfinite(measured):
        return audio
    # In float32, with ONE scratch buffer: the song at the length cap is
    # 339 MB as float64, and holding a gained copy, the limiter's own arrays
    # and its output beside it measured 2.76 GB of peak RSS at 480 s.
    # 16-bit is what leaves here; float32 is 24.
    src = np.asarray(audio, dtype=np.float32)
    out = np.empty_like(src)
    ceiling_db = 20 * math.log10(MASTER_CEILING)
    gain_db = lufs - measured
    for _ in range(MASTER_PASSES):
        _limit_into(out, src, 10 ** (gain_db / 20), MASTER_CEILING)
        over = true_peak_db(out) - ceiling_db
        if over > 0:
            out *= np.float32(10 ** (-over / 20))
        short = lufs - integrated_loudness(out)
        if abs(short) <= MASTER_TOLERANCE:
            break
        gain_db += short
    return out


_LIMIT_BLOCKS = 4096  # lookahead blocks `_limit_into` works through at a time


def _limit_into(
    out: np.ndarray,
    src: np.ndarray,
    gain: float,
    ceiling: float,
    lookahead: float = 0.005,
    release: float = 0.05,
) -> None:
    """`out[:] = limit(src * gain)`: `effects._limit` as a one-shot
    (`loop=False`) — each lookahead block's gain accounts for the block
    ahead, the duck is instant, only the recovery is smoothed, from the
    first gain value — worked through a piece at a time, so nothing the
    size of the song is made beside `out`. The gain envelope is float64 and
    the same recurrence; the audio is whatever `out` is (float32)."""
    n = len(src)
    w = max(1, n_samples(lookahead, SR))
    per = _LIMIT_BLOCKS * w
    blocks = np.empty(-(-n // w))
    for a in range(0, n, per):
        piece = np.abs(src[a : a + per])
        piece = (piece.max(axis=1) if piece.ndim == 2 else piece).astype(np.float64)
        pad = (-len(piece)) % w
        if pad:
            piece = np.concatenate([piece, np.zeros(pad)])
        blocks[a // w : a // w + len(piece) // w] = piece.reshape(-1, w).max(axis=1)
    blocks *= abs(gain)
    ahead = np.roll(blocks, -1)
    ahead[-1] = 0.0  # a one-shot: nothing lies ahead of the last block
    duck = np.minimum(1.0, ceiling / np.maximum(np.maximum(blocks, ahead), 1e-9))
    r = float(np.exp(-1.0 / max(release * SR, 1.0)))
    b, a_ = [1.0 - r], [1.0, -r]
    state = lfilter_zi(b, a_) * float(duck[0])
    for a in range(0, n, per):
        g = np.repeat(duck[a // w : a // w + _LIMIT_BLOCKS], w)[: n - a]
        smooth, state = lfilter(b, a_, g, zi=state)
        np.minimum(g, smooth, out=g)
        g *= gain
        g = g.astype(out.dtype)
        np.multiply(
            src[a : a + per], g[:, None] if out.ndim == 2 else g, out=out[a : a + per]
        )


def loudness_report(audio: np.ndarray) -> dict:
    """What an export measures: integrated loudness (LUFS), sample peak
    (dBFS) and true peak (dBTP), each rounded to 0.1 — None where there is
    nothing to measure (JSON and a header have no -inf)."""

    def tidy(v: float) -> float | None:
        return round(v, 1) if math.isfinite(v) else None

    return {
        "lufs": tidy(integrated_loudness(audio)),
        "peak_db": tidy(peak_db(audio)),
        "true_peak_db": tidy(true_peak_db(audio)),
    }


# --- stems ---------------------------------------------------------------------


def _sounds(song: dict, track: dict) -> bool:
    """Whether a track plays anything in the mix: audible (`sm.audible`:
    unmuted, and soloed when any track is), with a cell (drums, synth) or a
    take (audio) in some section."""
    if not sm.audible(song, track):
        return False
    if track["kind"] == "audio":
        if any(not x["mute"] for x in sm.long_items(song, track["id"])):
            return True  # a whole stem on the timeline (long audio)
        return any(
            a["track_id"] == track["id"] for s in song["sections"] for a in s["audio"]
        )
    return any(s["cells"].get(track["id"]) is not None for s in song["sections"])


def solo_song(song: dict, track_id) -> dict:
    """The song with only `track_id` playing, whatever the mix says: every
    other track muted, this one unmuted, no solo left to silence it. What
    the drawer's "solo this cell" renders a section of (`routes/song.py`).
    Like a stem (`stem_songs`) a muted track keeps its cells, takes and
    clocks, so each section keeps its cycle and the audition lines up with
    the mix; unlike a stem the track sounds even when the mix mutes it."""
    solo = copy.deepcopy(song)
    for track in solo["tracks"]:
        track["mute"] = track["id"] != track_id
        track["solo"] = False
    return solo


def stem_songs(song: dict) -> list[tuple[str, dict]]:
    """One (entry name, song) per sounding track, in track order. The name
    is fixed by kind and index — `drums`, `synth-N`, `audio-N`, N counting
    that kind's tracks from 1 — never the track's own (user-supplied) name.

    Each song is the whole song with every other track muted. A muted
    track keeps its cells, takes and pattern clocks (`section_loop` drops
    only the sound), so every section keeps its main lane, its cycle and
    its timeline, and a stem lines up with the mix sample for sample."""
    out: list[tuple[str, dict]] = []
    count: dict[str, int] = {}
    for track in song["tracks"]:
        kind = track["kind"]
        count[kind] = count.get(kind, 0) + 1
        if not _sounds(song, track):
            continue
        name = "drums" if kind == "drums" else f"{kind}-{count[kind]}"
        solo = copy.deepcopy(song)
        for other in solo["tracks"]:
            if other["id"] != track["id"]:
                other["mute"] = True
        out.append((f"{name}.wav", solo))
    return out
