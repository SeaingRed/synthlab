"""Every held sound must meet itself at the loop point.

A WAV-fallback key in hold mode loops the sustain of its `?hold=1` render
at the points `holdloop.loop_points` picks, with `holdloop.bake_loop`'s
crossfade. The bar: across the seam, the level may not change by more than
the sound's own movement does. That is the 95th percentile of level steps
between adjacent 30 ms windows inside the loop span (1 dB floor for very
steady sounds), plus a 0.6 dB tolerance.

Measured over every hold-looped factory and library sound at
C3 and C4 (178 checks): the geometry before the hold-window fix failed 50
of them (42 sounds, up to 9 dB over the bar); today's passes all 178, the
closest 0.53 dB over the bare bar. A regression toward the old click fails
here long before anyone hears it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.core import SR, note_to_hz
from synthlab.studio.factory import FACTORY
from synthlab.studio.holdloop import LOOP_XFADE, bake_loop, loop_points
from synthlab.studio.recipes import RECIPES
from synthlab.studio.server import create_app
from synthlab.studio.soundmeta import live_note_duration
from synthlab.synth import Patch, Synth

LIBRARY = Path("synthlab/studio/presets/library.json")
WIN = int(0.03 * SR)
FLOOR_DB, TOLERANCE_DB = 1.0, 0.6


def _sounds() -> list[tuple[str, Patch]]:
    lib = json.loads(LIBRARY.read_text(encoding="utf-8"))
    out = [("library:" + e["patch"]["name"], Patch.from_dict(e["patch"])) for e in lib]
    out += [("factory:" + f["name"], Patch.from_dict(f["patch"])) for f in FACTORY]
    for r in RECIPES:  # a recipe's main patch and each of its layers
        out.append(("recipe:" + r["name"], Patch.from_dict(r["patch"])))
        out += [
            (f"recipe:{r['name']}/{lay['role']}", Patch.from_dict(lay["patch"]))
            for lay in r["layers"] or []
        ]
    # The worklet plays all of them, but the hold loop is
    # still what any of them plays whenever a key takes the WAV path: before
    # the live voice is up, and for a patch or FX chain it refuses. So every
    # sound with a sustain to hold is swept, not just the ones it can't play.
    return [(n, p) for n, p in out if p.sustain > 0.05]


def _step_db(after: np.ndarray, before: np.ndarray) -> float:
    rms = lambda x: np.sqrt(np.mean(x**2)) + 1e-12  # noqa: E731
    return float(abs(20 * np.log10(rms(after) / rms(before))))


def _overshoot(p: Patch, pitch: str) -> float:
    """dB by which the worst channel's seam exceeds its own movement."""
    raw = np.asarray(Synth(p).note(pitch, live_note_duration(p, hold=True)))
    loop = loop_points(p, len(raw), note_to_hz(pitch))
    out, a, b = bake_loop(raw, loop), loop.start, loop.end
    worst = -np.inf
    for c in range(raw.shape[1]):
        seg = out[a:b, c]
        seam = _step_db(seg[:WIN], seg[-WIN:])  # after the wrap vs before it
        src = raw[a:b, c]
        own = [
            _step_db(src[i + WIN : i + 2 * WIN], src[i : i + WIN])
            for i in range(0, len(src) - 2 * WIN, WIN // 2)
        ]
        worst = max(worst, seam - max(FLOOR_DB, float(np.percentile(own, 95))))
    return worst


def test_every_hold_looped_sound_meets_itself():
    sounds = _sounds()
    # the sweep is actually sweeping
    assert len(sounds) > 80
    over = [
        f"{name} @ {pitch}: {d:+.1f} dB"
        for name, p in sounds
        for pitch in ("C3", "C4")
        if (d := _overshoot(p, pitch)) > TOLERANCE_DB
    ]
    assert not over, "held loops that click at the seam:\n" + "\n".join(over)


def test_loop_is_whole_periods_and_ends_before_the_release():
    p = Patch(attack=0.3, decay=0.4, sustain=0.8, release=1.0)
    n = round(live_note_duration(p, hold=True) * SR)
    loop = loop_points(p, n, 261.6256)
    periods = (loop.end - loop.start) / SR * 261.6256
    assert abs(periods - round(periods)) < 0.01 * SR / 261.6256
    assert loop.start >= round(0.7 * SR) and loop.end <= loop.gate
    assert loop.gate == round((n / SR - 1.0) * SR)


def test_baked_seam_is_continuous():
    p = replace_unison(Patch(attack=0.1, decay=0.2, sustain=0.9, release=0.5))
    raw = np.asarray(Synth(p).note("C4", live_note_duration(p, hold=True)))
    loop = loop_points(p, len(raw), note_to_hz("C4"))
    out = bake_loop(raw, loop)
    jump = np.abs(out[loop.start] - out[loop.end - 1]).max()
    typical = np.percentile(np.abs(np.diff(raw[loop.start : loop.end], axis=0)), 99)
    assert jump <= typical  # the wrap is just another sample step
    # only the crossfade span changed
    n = round(LOOP_XFADE * SR)
    assert np.array_equal(out[: loop.end - n], raw[: loop.end - n])
    assert np.array_equal(out[loop.end :], raw[loop.end :])


def replace_unison(p: Patch) -> Patch:
    from dataclasses import replace

    return replace(p, unison=3, unison_detune=17.0)


def test_hold_route_sends_the_loop_it_baked(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    held = client.get("/note/C4.wav?hold=1")
    one = client.get("/note/C4.wav")
    assert "x-loop" not in one.headers  # a one-shot is not looped
    start, end, gate = (float(v) for v in held.headers["x-loop"].split(","))
    patch = client.app.state.studio.patch
    n = round(live_note_duration(patch, hold=True) * SR)
    expect = loop_points(patch, n, note_to_hz("C4"))
    assert (round(start * SR), round(end * SR), round(gate * SR)) == tuple(expect)
