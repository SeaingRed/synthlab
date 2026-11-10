"""Known-answer material for the Sample Lab's tests, rendered by the engine
itself: a loop at a stated tempo in a stated key, with each part available
on its own (the true "stems" of the mix).

No audio file ships with the repo and no test downloads one, so everything
the analysis and the separator are checked against is built here.
"""

from __future__ import annotations

import numpy as np

from synthlab import Song, Synth, preset
from synthlab.core import SR
from synthlab.sequencer import chord

# key -> (one chord per bar, the bass root per bar): i-iv-v-i / I-IV-V-I,
# which names its key without leaning on the relative one
PROGRESSIONS = {
    "A minor": (
        [("A3", "min"), ("D3", "min"), ("E3", "min"), ("A3", "min")],
        ["A2", "D3", "E3", "A2"],
    ),
    "C major": (
        [("C3", "maj"), ("F3", "maj"), ("G3", "maj"), ("C3", "maj")],
        ["C3", "F2", "G2", "C3"],
    ),
    "F# minor": (
        [("F#2", "min"), ("B2", "min"), ("C#3", "min"), ("F#2", "min")],
        ["F#2", "B2", "C#3", "F#2"],
    ),
}
# A tight kick. The engine's default (0.42 s decay, driven) is a sustained
# 52 Hz note with a third harmonic on D#: at 100 bpm it turned C major into
# C minor (the key detector's docstring lists it as a known limit).
KICK = {"decay": 0.2, "drive": 1.0}


def build(bpm: float, key: str, bars: int = 8, vocal: bool = False) -> Song:
    """Drums, a bass line on the roots and a keyboard on the chords; with
    `vocal`, a formant ("ah"-like) lead on chord tones as a fourth part."""
    song = Song(bpm=bpm)
    prog, roots = PROGRESSIONS[key]
    bass = Synth(preset("bass_reese"))
    keys = Synth(preset("keys_ep"))
    song.drum("drums", "kick", "x...x...x...x...", bars=bars, **KICK)
    song.drum("drums", "snare", "....x.......x...", bars=bars, gain=0.7)
    song.drum("drums", "hat", "..x...x...x...x.", bars=bars, gain=0.4)
    for b in range(bars):
        root, (name, quality) = roots[b % 4], prog[b % 4]
        line = [(root, 1.0), (root, 0.5), (root, 0.5), (root, 1.0), (root, 1.0)]
        song.notes("bass", bass, line, start_beat=b * 4, gain=0.8)
        stab = chord(name, quality, octave_shift=1)
        song.notes("other", keys, [(stab, 2.0)] * 2, start_beat=b * 4, gain=0.6)
    if vocal:
        voice = Synth(
            preset("lead_supersaw").tweak(
                unison=1,
                formant_amount=0.9,
                formant_vowel=0.1,
                vibrato_depth=0.25,
                attack=0.06,
                release=0.2,
            )
        )
        for b in range(bars):
            tones = chord(prog[b % 4][0], prog[b % 4][1], octave_shift=2)
            line = [(tones[b % 3], 1.5), (tones[(b + 1) % 3], 0.5), (None, 0.5)]
            line += [(tones[(b + 2) % 3], 1.5)]
            song.notes("vocals", voice, line, start_beat=b * 4, gain=0.5)
    return song


def loop(bpm: float, key: str, bars: int = 8, lead_in: float = 0.0) -> np.ndarray:
    """The mix, exactly `bars` long, after `lead_in` seconds of silence."""
    x = build(bpm, key, bars).render(tail_beats=0)
    x = x[: int(round(bars * 4 * 60.0 / bpm * SR))]
    if lead_in:
        x = np.concatenate([np.zeros((int(lead_in * SR), 2)), x])
    return x


# (MIDI note or None for silence, seconds): a short note, a long held one
# a few cents sharp, another short one
VOICE_LINE = [(57, 0.3), (None, 0.3), (60.12, 1.5), (None, 0.3), (64, 0.4), (None, 0.2)]
VOICE_HELD = 60.12


def voice_line() -> np.ndarray:
    """A sung-like line on its own, stereo: each note a harmonic series
    under a fixed vowel (three formant bumps — the throat stays put while
    the pitch moves, which is what "formant" means), with a singer's
    vibrato on the long note, a soft attack and release, a little breath,
    and real silence between notes. Three "syllables", one note worth
    looping: what the vocal instrument and the chops are checked against."""
    rng = np.random.default_rng(7)
    out = []
    for note, seconds in VOICE_LINE:
        n = int(seconds * SR)
        if note is None:
            out.append(np.zeros(n))
            continue
        t = np.arange(n) / SR
        depth = 0.25 if seconds > 1 else 0.0  # semitones of vibrato
        midi = note + depth * np.sin(2 * np.pi * 5.5 * t) * np.minimum(t / 0.3, 1.0)
        hz = 440.0 * 2.0 ** ((midi - 69.0) / 12.0)
        phase = 2 * np.pi * np.cumsum(hz) / SR
        x = np.zeros(n)
        for h in range(1, 24):
            f = h * hz.mean()
            vowel = sum(
                g * np.exp(-0.5 * ((f - c) / w) ** 2)
                for c, w, g in ((700, 130, 1.0), (1200, 160, 0.6), (2600, 250, 0.3))
            )
            x += (0.08 + vowel) / h * np.sin(h * phase)
        env = np.minimum(1.0, np.minimum(t / 0.03, (seconds - t) / 0.06))
        out.append((x * 0.25 + rng.standard_normal(n) * 0.002) * env)
    mono = np.concatenate(out)
    return np.column_stack([mono, mono])


def drum_loop(
    rows: dict[str, str],
    bpm: float = 120.0,
    swing: float = 0.0,
    vel: dict[str, float] | None = None,
    params: dict[str, dict] | None = None,
) -> tuple[np.ndarray, list[list]]:
    """(audio, the pattern's steps): a drum pattern written one string a
    voice ("x...x...", 16 steps a bar, as many bars as the strings are
    long) rendered by the studio's own drum machine — its swing, its
    velocity lane, the open hat choked by the next closed one — exactly as
    long as its bars. The ground truth the drum transcription is measured
    against."""
    from synthlab.studio.arrange import _schedule_drums
    from synthlab.studio.state import Arrangement

    length = max(len(r) for r in rows.values())
    bars = max(1, length // 16)
    steps = sorted(
        [voice, i]
        for voice, row in rows.items()
        for i, ch in enumerate(row)
        if ch == "x"
    )
    arr = Arrangement(
        bpm=bpm,
        cells=[],
        drums=steps,
        drum_bars=bars,
        drum_steps=16,
        drum_swing=swing,
        drum_vel=dict(vel or {}),
        drum_params={v: dict(p) for v, p in (params or {}).items()},
    )
    song = Song(bpm=bpm)
    _schedule_drums(song, arr)
    x = song.render(tail_beats=0)
    n = int(round(bars * 240.0 / bpm * SR))
    if len(x) < n:
        x = np.concatenate([x, np.zeros((n - len(x), 2))])
    return x[:n], steps


_HZ = {"C": 130.81, "D": 146.83, "E": 164.81, "F": 174.61, "G": 196.0}
_HZ |= {"A": 220.0, "Bb": 233.08}
PAIR_BAR = 2.0  # seconds a bar of `pair_song`


def tones(names: str, seconds: float, octave: float = 1.0) -> np.ndarray:
    """Sines on the named notes (octave 3; `octave` 0.5 is an octave down)."""
    t = np.arange(int(seconds * SR)) / SR
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.02)
    return sum(np.sin(2 * np.pi * _HZ[n] * octave * t) for n in names.split()) * fade


def pair_song(bass_roots: str, bars: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """(chords, bass), mono: F, C, D minor and B flat a bar each — the seven
    notes F major and D minor share — over a bass that plays one root a
    bar. Which of the two keys it is, is the bass's to say."""
    chords = ["F A C", "C E G", "D F A", "Bb D F"]
    roots = bass_roots.split()
    pads = np.concatenate([tones(chords[b % 4], PAIR_BAR) for b in range(bars)])
    bass = np.concatenate(
        [tones(roots[b % len(roots)], PAIR_BAR, 0.5) for b in range(bars)]
    )
    return pads * 0.2, bass * 0.5


def stems(song: Song, seconds: float) -> dict[str, np.ndarray]:
    """Each track of `song` on its own, `seconds` long: summed, they are
    the un-normalised mix."""
    n = int(seconds * SR)
    out = {}
    for name, track in song.tracks.items():
        buf = track.render(n, SR)
        if len(buf) < n:
            buf = np.concatenate([buf, np.zeros((n - len(buf), 2))])
        out[name] = buf[:n]
    return out
