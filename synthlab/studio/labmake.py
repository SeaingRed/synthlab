"""What a region of a separated song becomes, beyond a plain clip or
sampler sound (`lab.py`): a vocal you can play, vocal chops, a drum
pattern, a kit, a take on the song.

Each of these is a reading of **one stem** — the vocals, or the drums — so
`stem_region` hands over that stem's audio for the region whatever boxes
are ticked, and falls back to what the region plays (the mix, usually)
when the song has not been separated: an a cappella or a drum loop
imported on its own works as it is, and for anything else the result says
it was read from the mix.

Nothing here adds an engine parameter. A vocal instrument is the sampler's
own fields set from what `analysis.voiced` found (start, end, a loop inside
the held note with its crossfade, the root, formant mode); vocal chops are
`sampler_slices`; a drum pattern is the drum grid's own `drums` /
`drum_vel` / `drum_swing` (or a pool pattern of the song), written by the
routes through the paths a hand edit takes; a kit is clips on the drum
rows' existing sample slots (`Arrangement.drum_samples`).
"""

from __future__ import annotations

import math

import numpy as np

from .. import sampler
from ..analysis import drumhits, voiced
from ..core import SR
from ..sequencer import name_of
from . import lab, limits, sources

# the vocal instrument: where in the held note the loop sits, and how it plays
_LOOP_IN, _LOOP_OUT = 0.25, 0.1  # of the run, kept clear of its start and end
_LOOP_SECONDS = (0.08, 0.5)  # the loop's length: 40% of the run, within these
_PRE_ROLL = 0.01  # seconds before the run the note starts from
_XFADE = 0.03  # seconds
_MAX_DETUNE = 25.0  # cents: `osc1_detune`'s range
VOCAL_ENVELOPE = {"attack": 0.015, "decay": 0.3, "sustain": 0.9, "release": 0.25}
MAX_PATTERN_BARS = max(limits.BAR_CHOICES)
KIT_LANES = drumhits.LANES


def stem_region(data_dir, meta: dict, stem: str):
    """(audio, where it was read from, "" or why not): the region of
    `stem` when the song is separated, else of what the region plays."""
    if lab.too_long(meta):
        return None, "", lab._too_long_message(meta)
    start, length = lab.span(meta)
    first, frames = int(round(start * SR)), int(round(length * SR))
    if meta["stems"]["model"]:
        audio, where = sources.read(data_dir, meta, stem, first, frames), stem
    else:
        audio, where = lab.region_audio(data_dir, meta), lab.what(meta)
    if not len(audio) or not np.any(np.abs(audio) > 1e-4):
        return None, where, f"The {where} is silent in that region."
    return audio, where, ""


# --- vocals --------------------------------------------------------------------


def _steadiest(audio: np.ndarray, lo: int, hi: int, length: int) -> int:
    """Where in [lo, hi) a window `length` samples long holds its level
    best (the least spread of its 10 ms loudness, in dB): a singer swells
    into a note and lets it fall, and a loop over that is a tremolo at the
    loop's rate."""
    hop = SR // 100
    mono = np.asarray(audio, dtype=np.float64)
    mono = mono.mean(axis=1) if mono.ndim == 2 else mono
    frames = (hi - lo) // hop
    need = max(1, length // hop)
    if frames <= need:
        return lo
    seg = mono[lo : lo + frames * hop].reshape(frames, hop)
    db = 10.0 * np.log10(np.mean(seg * seg, axis=1) + 1e-10)
    windows = np.lib.stride_tricks.sliding_window_view(db, need)
    spread = windows.max(axis=1) - windows.min(axis=1)
    return lo + int(np.argmin(spread)) * hop


def vocal_fields(run: voiced.Run, audio: np.ndarray) -> dict:
    """The Patch fields that make a held note (`run`, found in `audio`) a
    playable voice: the note starts just before the run and loops inside
    it — over the stretch where its level is steadiest, a whole number of
    the note's own periods long so the seam joins like to like, crossfaded
    — with the root on its pitch, the few cents it is off the key taken
    out, and formant mode on, so a higher key is a higher note in the same
    throat rather than a smaller singer."""
    total = len(audio)
    period = SR / sampler.midi_hz(run.midi)
    span = run.end - run.start
    inner = (run.start + int(span * _LOOP_IN), run.end - int(span * _LOOP_OUT))
    length = int(min(max(_LOOP_SECONDS[0] * SR, span * 0.4), _LOOP_SECONDS[1] * SR))
    length = min(length, inner[1] - inner[0])
    lo = _steadiest(audio, inner[0], inner[1], length)
    hi = lo + max(2, int(length / period)) * period
    end = max(hi, run.end)
    return {
        "sampler_root": run.root,
        "sampler_start": max(0.0, run.start - _PRE_ROLL * SR) / total,
        "sampler_end": min(1.0, end / total),
        "sampler_loop": "on",
        "sampler_loop_start": lo / total,
        "sampler_loop_end": min(1.0, hi / total),
        "sampler_xfade": min(_XFADE, (hi - lo) / SR / 4.0),
        "sampler_mode": "formant",
        "osc1_detune": float(min(_MAX_DETUNE, max(-_MAX_DETUNE, -run.cents))),
        **VOCAL_ENVELOPE,
    }


def vocal_instrument(audio: np.ndarray) -> tuple[dict, str, bool]:
    """(Patch fields, what to tell the player, was a held note found). With
    no steady pitched note in the region there is nothing to loop or tune:
    the fields are empty — the whole region on the keys, tape-style — and
    the message says so."""
    run = voiced.best_run(audio)
    if run is None:
        return (
            {},
            "No steady sung note in that region: the whole region is on the "
            "keys, tape-style. Pick a region with a held note to make a voice.",
            False,
        )
    off = f", {run.cents:+.0f} cents (tuned out)" if abs(run.cents) >= 5 else ""
    said = (
        f"Voice from a held {name_of(run.root)}{off}: {run.seconds:.2f} s at "
        f"{run.start / SR:.2f} s into the region, {run.clarity * 100:.0f}% "
        "periodic. It loops inside that note and keeps its formants."
    )
    return vocal_fields(run, audio), said, True


def vocal_chops(audio: np.ndarray) -> tuple[str, str]:
    """(the sampler's slices string, what to tell the player): one chop a
    syllable or phrase, in time order up the keys from the root. "" when
    the region has fewer than two."""
    spans = voiced.syllables(audio, sampler.MAX_SLICES)
    n = len(audio)
    slices = [sampler.Slice(a / n, b / n) for a, b in spans if b - a > 32]
    if len(slices) < 2:
        return "", "No separate syllables in that region: it is on the keys whole."
    said = (
        f"{len(slices)} vocal chops, one a key from C4 up, in the order they are sung."
    )
    if len(slices) == sampler.MAX_SLICES:
        said += f" (A kit holds {sampler.MAX_SLICES}: the weakest breaks were joined.)"
    return sampler.format_slices(slices), said


# --- drums ---------------------------------------------------------------------


def pattern_bars(meta: dict) -> tuple[int, str]:
    """(bars a drum pattern takes from the region, "" or why none): a
    pattern needs the region on the bar lines, and the drum grid holds
    `MAX_PATTERN_BARS` — a longer region gives its first bars, and one the
    song's end cut short gives the whole bars it has."""
    g = lab.grid(meta)
    if not g["set"] or meta["region"]["mode"] != "bars":
        return 0, "Pick the region in bars first: a pattern sits on the bar lines."
    # the bars the region really holds: the song's end cuts a region short
    held = max(1, int(lab.span(meta)[1] / g["bar"] + 0.02))
    return max(b for b in limits.BAR_CHOICES if b <= held), ""


def loop_tempo(meta: dict) -> float | None:
    """The source's tempo as the loop can hold it — a whole bpm inside
    `limits.BPM_RANGE`, as the transport shows and posts it — or None when
    the source has no grid or its tempo is outside the range. A beat read
    from a song is steps on a grid: at any other tempo it is another
    beat (a 79.7 bpm song's, left at the session's 200, ran 2.5x fast)."""
    g = lab.grid(meta)
    bpm = float(round(g["bpm"])) if g["set"] else 0.0
    lo, hi = limits.BPM_RANGE
    return bpm if lo <= bpm <= hi else None


def drum_pattern(meta: dict, audio: np.ndarray, bars: int):
    """(the transcription, its hits): `bars` bars from the region's start,
    on the Lab's grid."""
    bar = lab.grid(meta)["bar"]
    return drumhits.transcribe(audio[: int(math.ceil(bars * bar * SR))], bar, bars)


def pattern_summary(pat: drumhits.Pattern) -> str:
    """ "8 kick, 4 snare, 14 hat": what was written, by lane."""
    names = {"hat": "closed hat", "ohat": "open hat"}
    parts = [f"{n} {names.get(lane, lane)}" for lane, n in pat.counts.items()]
    return ", ".join(parts) if parts else "no hits"


def kit_hits(audio: np.ndarray, hits: list) -> dict[str, np.ndarray]:
    """One clean one-shot a lane, cut from the stem: lane -> audio, each
    faded out over its last 8 ms so a cut tail is not a click."""
    out: dict[str, np.ndarray] = {}
    for lane, (a, b) in drumhits.exemplars(hits).items():
        seg = np.array(audio[int(a * SR) : int(b * SR)], dtype=np.float64)
        if len(seg) < 256:
            continue
        fade = min(len(seg), int(0.008 * SR))
        seg[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]
        out[lane] = seg
    return out


# --- the song ------------------------------------------------------------------


def song_take(data_dir, meta: dict, song_bpm: float):
    """(audio, clip meta, bars, was it fitted, "" or why not): the region
    as a take for the song — stretched to the song's tempo and then held to
    whole bars of it, cut or padded with silence, so it starts and ends on
    the song's bar lines. A region with no grid (or a tempo too far to
    stretch) goes as it is, at its own speed, and the caller says so."""
    audio, info, problem = lab.clip_audio(data_dir, meta, song_bpm, fit=True)
    if audio is None:
        return None, {}, 0, False, problem
    plan = lab.stretch_plan(meta, song_bpm)
    if not plan["possible"]:
        return audio, info, 0, False, ""
    bar = 240.0 / song_bpm
    bars = info.get("bars") or max(1, int(round(len(audio) / SR / bar)))
    n = int(round(bars * bar * SR))
    if n / SR > limits.MAX_CLIP_SECONDS:
        return (
            None,
            {},
            0,
            False,
            (
                f"{bars} bars at the song's {song_bpm:g} bpm is longer than "
                f"{limits.MAX_CLIP_SECONDS:g} s: pick fewer bars."
            ),
        )
    if len(audio) < n:
        audio = np.concatenate([audio, np.zeros((n - len(audio), 2))])
    info = {**info, "bars": bars, "bpm": round(song_bpm, 3)}
    return audio[:n], info, bars, True, ""
