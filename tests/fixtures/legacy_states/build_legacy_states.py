"""How the frozen v2 state fixtures in this directory were made.

Each fixture is a real `state.json` payload written by the *pre-song-model*
studio (STATE_VERSION 2): scenes come from
`capture_scene`, the bank from `bank_save`/`bank_load`, layers from
`select_layer`, and clip lanes and one-shot slots from the same fields
the routes write. They are frozen as JSON so the v2 -> v3 migration
(`StudioState._migrate`) is tested against what the old code really
produced, not against a hand-written guess at it.

This script only runs against that old code (`capture_scene` there
appends to `s.scenes`); it is kept to show how the fixtures were made,
not to be re-run.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from synthlab.studio.state import StudioState


def _fresh() -> StudioState:
    return StudioState(Path(tempfile.mkdtemp()))


def _payload(s: StudioState) -> dict:
    return json.loads(json.dumps(s._payload()))


def with_scenes() -> dict:
    """Four captured scenes over two sounds, a modified bank, three layers,
    FX on the main lane and the drum bus, a curve, and a running order
    that repeats one scene and leaves one out."""
    s = _fresh()
    # the bank: slot 6 overwritten with our own beat
    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["kick", 6], ["snare", 4], ["hat", 2], ["hat", 10]],
        drum_bars=1,
        drum_steps=16,
    )
    s.bank_save(6, "mine")
    s.fx_drums = [{"kind": "drive", "params": {"amount": 0.4}}]
    s.curves = {"cutoff": [[0.0, 0.2], [1.0, 0.8]]}
    # layer 1: its own sound and notes
    s.select_layer(1)
    s.patch = replace(s.patch, cutoff=2400.0, name="Bright")
    s.arrangement = replace(s.arrangement, cells=[["C2", 0], ["E2", 2]])
    s.layers[1]["fx"] = [{"kind": "reverb", "params": {"mix": 0.3}}]
    s.select_layer(0)
    s.patch = replace(s.patch, cutoff=700.0, name="Dark")
    s.layers[0]["fx"] = [{"kind": "delay", "params": {"mix": 0.2}}]
    s.arrangement = replace(s.arrangement, cells=[["A1", 0], ["A1", 2]])
    s.bank_load(0)
    s.capture_scene("intro")
    # verse: a 2-bar grid, velocities and a slide, Techno from the bank
    s.arrangement = replace(
        s.arrangement,
        bars=2,
        steps_per_bar=8,
        cells=[["A1", 0], ["C2", 3], ["E2", 8], ["A1", 12]],
        velocities={"3": 0.6},
        slides=[8],
    )
    s.bank_load(2)
    s.capture_scene("verse")
    # chorus: played from layer 1 (the main lane is now "Bright"), and the
    # intro's beat again — the migration must share it
    s.select_layer(1)
    s.arrangement = replace(
        s.arrangement, bars=1, steps_per_bar=4, velocities={}, slides=[]
    )
    s.bank_load(0)
    s.capture_scene("chorus")
    # bridge: a new sound on layer 1, our own beat, then dropped from the song
    s.patch = replace(s.patch, cutoff=5000.0, resonance=0.6, name="Brighter")
    s.bank_load(6)
    s.capture_scene("bridge")
    s.song = [
        {"scene": 0, "repeats": 1},
        {"scene": 1, "repeats": 2},
        {"scene": 2, "repeats": 1},
    ]
    return _payload(s)


def no_scenes() -> dict:
    """No scenes at all: a modified bank, the working loop on layer 1,
    notes on layer 0 (inactive) and a patch-only layer 2."""
    s = _fresh()
    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["kick", 8], ["clap", 4], ["clap", 12]],
        drum_vel={"8": 0.7},
    )
    s.bank_save(7, "clappy")
    s.arrangement = replace(s.arrangement, cells=[["A1", 0], ["E2", 3]])
    s.select_layer(2)
    s.patch = replace(s.patch, cutoff=3000.0, name="Unused")
    s.arrangement = replace(s.arrangement, cells=[])
    s.select_layer(1)
    s.patch = replace(s.patch, cutoff=1500.0, name="Lead")
    s.arrangement = replace(s.arrangement, cells=[["C2", 1], ["D2", 2]])
    s.bank_load(7)
    return _payload(s)


def with_clips() -> dict:
    """Clip lanes in two scenes and in the working loop, a drum one-shot
    slot, and a 2-bar drum clock."""
    s = _fresh()
    s.arrangement = replace(
        s.arrangement,
        drums=[["kick", 0], ["snare", 4], ["kick", 16], ["snare", 20]],
        drum_bars=2,
        drum_samples={"snare": "vox"},
        clips=[{"clip": "riser", "at_bar": 1, "gain": 0.8}],
    )
    s.capture_scene("build")
    s.arrangement = replace(
        s.arrangement,
        clips=[
            {"clip": "vox", "at_bar": 0, "gain": 1.0},
            {"clip": "riser", "at_bar": 1, "gain": 1.2},
        ],
    )
    s.capture_scene("drop")
    s.arrangement = replace(
        s.arrangement, clips=[{"clip": "outro", "at_bar": 0, "gain": 1.0}]
    )
    return _payload(s)


def divergent() -> dict:
    """Scenes that disagree with the session and with each other: "a" and
    "b" at 128 bpm on one kit, "c" at 100 bpm on another kit with its own
    master FX; the session was left at 90 bpm on a third kit. A song has one
    tempo, kit and master chain, so the migration has to pick."""
    s = _fresh()
    mfx = [{"kind": "reverb", "params": {"mix": 0.25}}]
    s.fx_master = mfx
    s.fx_drums = [{"kind": "drive", "params": {"amount": 0.3}}]
    s.arrangement = replace(
        s.arrangement,
        bpm=128.0,
        swing=0.1,
        drums=[["kick", 0], ["kick", 8], ["hat", 4], ["hat", 12]],
        drum_params={"kick": {"decay": 0.4}},
        drum_mix={"hat": {"level": 0.7}},
    )
    s.capture_scene("a")
    s.arrangement = replace(s.arrangement, cells=[["C2", 0], ["E2", 1]])
    s.capture_scene("b")
    s.arrangement = replace(
        s.arrangement,
        bpm=100.0,
        root="E2",
        drum_params={"kick": {"decay": 0.9}},
    )
    s.fx_drums = [{"kind": "bitcrush", "params": {"bits": 8.0}}]
    s.fx_master = [{"kind": "delay", "params": {"mix": 0.4}}]
    s.capture_scene("c")
    # the session moves on: another tempo and kit, the first master chain
    s.arrangement = replace(
        s.arrangement, bpm=90.0, root="A1", drum_params={"snare": {"decay": 0.3}}
    )
    s.fx_drums = []
    s.fx_master = mfx
    return _payload(s)


FIXTURES = {
    "with_scenes.json": with_scenes,
    "divergent.json": divergent,
    "no_scenes.json": no_scenes,
    "with_clips.json": with_clips,
}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    for name, build in FIXTURES.items():
        (out / name).write_text(json.dumps(build(), indent=2), encoding="utf-8")
        print("wrote", out / name)
