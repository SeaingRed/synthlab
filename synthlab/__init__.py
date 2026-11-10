"""
synthlab — a hackable software synthesizer and music toolkit in pure NumPy.

    from synthlab import Synth, Song, preset, audio, effects as fx

    synth = Synth(preset("lead_supersaw"))
    song = Song(bpm=124)
    song.notes("lead", synth, [("C4", 1), ("D#4", 0.5), ("G4", 1.5)])
    song.drum("drums", "kick", "x...x...x...x...", bars=2)
    audio.save("out.wav", song.render(master_fx=[fx.limit(loop=False)]))

Modules
    core        oscillators, envelopes, filters, LFOs, note maths
    effects     drive, delay, reverb, chorus, compression, sidechain, limiter
    synth       Patch dataclass, Synth renderer, preset library
    drums       synthesised drum kit (kick, snare, hats, clap, toms...)
    sequencer   Song timeline, scales, chords, Euclidean rhythms
    audio       WAV I/O, real-time playback, analysis
    midifile    Standard MIDI Files, written and read (notes and tempo)
    analysis    feature extraction, bass/mono/phone-speaker analysis
    explore     patch breeding, steering, the sound-space store
"""

from . import analysis, audio, core, drums, effects, explore, sequencer, synth
from .core import SR, adsr, lfo, note_to_hz, osc
from .sequencer import Song, chord, euclid, scale
from .synth import PRESETS, Patch, Synth, list_presets, preset

__version__ = "0.1.0a1"

__all__ = [
    "SR",
    "Patch",
    "Synth",
    "Song",
    "PRESETS",
    "preset",
    "list_presets",
    "scale",
    "chord",
    "euclid",
    "osc",
    "adsr",
    "lfo",
    "note_to_hz",
    "core",
    "effects",
    "synth",
    "drums",
    "sequencer",
    "audio",
    "analysis",
    "explore",
]
