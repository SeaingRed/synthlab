"""How the finished loop is assembled: seams and level.

A loop is not a clip — it is a clip that has to meet itself. Two defects
lived in that join. The FX chains ran *after* the wrap-tile, so every
reverb and delay tail was cut dead at the loop point (a click you hear
once a bar, forever), and the whole mix was scaled by its single loudest
sample, so one transient set the volume of everything around it.
"""

from __future__ import annotations

import numpy as np

from synthlab.core import SR
from synthlab.studio.render import RenderInputs, normalize_master, render_audio
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

# A held note across the whole bar: its own release has to fold over the join.
HELD = Arrangement(
    bpm=120,
    bars=1,
    steps_per_bar=4,
    cells=[["A2", 0], ["A2", 1], ["A2", 2], ["A2", 3]],
)
# One note in the middle of the bar, so the top of the loop is silence and
# anything heard there arrived by wrapping round from the end.
MID_BAR = Arrangement(bpm=120, bars=1, steps_per_bar=4, cells=[["A2", 2]])
PATCH = Patch(name="seam", release=0.3)
REVERB = [{"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5}}]


def head_rms(audio: np.ndarray, samples: int = 8000) -> float:
    return float(np.sqrt((audio[:samples] ** 2).mean()))


def rms_around(audio: np.ndarray, skip: slice) -> float:
    """Level of everything except one excluded stretch."""
    return float(np.sqrt((np.delete(audio, skip, 0) ** 2).mean()))


def seam_ratio(audio: np.ndarray) -> float:
    """How big the jump from the last sample to the first is, measured in
    ordinary sample steps. A seamless loop is around 1; a cut tail is not."""
    mono = audio.mean(axis=1)
    edge = abs(float(mono[-1] - mono[0]))
    typical = float(np.abs(np.diff(mono)).mean())
    return edge / max(typical, 1e-12)


def test_a_held_note_joins_cleanly_at_the_loop_point():
    """The baseline every other path has to match: the note's own release
    folds over the join, so the last sample runs into the first."""
    inp = RenderInputs(patch=PATCH, arr=HELD, parts=("synth",))
    assert seam_ratio(render_audio(inp)) < 1.5


def test_a_master_reverb_tail_is_heard_at_the_top_of_the_loop():
    """Master FX ran on the already-tiled loop and were then truncated at n,
    so the tail was chopped at the seam instead of wrapping onto the start.
    With one note mid-bar the loop opens in silence: whatever is audible
    there got round the loop, and nothing did before."""
    dry = render_audio(RenderInputs(patch=PATCH, arr=MID_BAR, parts=("synth",)))
    wet = render_audio(
        RenderInputs(patch=PATCH, arr=MID_BAR, parts=("synth",), master_fx=REVERB)
    )
    assert head_rms(dry) < 1e-4, "fixture expects a silent top of loop"
    assert head_rms(wet) > 0.01, "the reverb tail was cut at the loop point"
    assert seam_ratio(wet) < 1.5, f"seam jumps {seam_ratio(wet):.1f}x"


def test_a_layer_reverb_tail_is_heard_at_the_top_of_the_loop():
    """Same defect one level down: each stem's chain baked in after the tile,
    so a reverb on the layer cut at the loop point too."""
    wet = render_audio(
        RenderInputs(patch=PATCH, arr=MID_BAR, parts=("synth",), main_fx=REVERB)
    )
    assert head_rms(wet) > 0.01, "the reverb tail was cut at the loop point"
    assert seam_ratio(wet) < 1.5, f"seam jumps {seam_ratio(wet):.1f}x"


def test_the_limiter_does_not_leave_the_loop_quieter_at_its_start():
    """normalize_master limits the mix, and the limiter's own gain envelope
    has to be periodic: limiting a single pass started at full gain, ducked
    the loop's opening attack and left a step in level at every repeat."""
    audio = render_audio(RenderInputs(patch=PATCH, arr=HELD, parts=("synth",)))
    assert seam_ratio(audio) < 1.5


def test_one_stray_transient_does_not_set_the_level_of_the_whole_loop():
    """Peak normalization handed the volume of the entire mix to its single
    loudest sample: a 12-sample tick made everything around it 20 dB
    quieter. The body of the mix is what the level should follow."""
    n = SR
    body = 0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / SR)
    steady = np.column_stack([body, body])
    spiked = steady.copy()
    spiked[n // 2 : n // 2 + 12] += 3.0

    plain = normalize_master(steady)
    ticked = normalize_master(spiked)
    around = slice(n // 2, n // 2 + 400)
    quiet, loud = rms_around(ticked, around), rms_around(plain, around)
    assert quiet > loud * 0.8, f"the tick cost {20 * np.log10(quiet / loud):.1f} dB"


def test_normalize_master_never_leaves_the_signal_over_full_scale():
    n = 4096
    hot = np.column_stack([np.sin(np.linspace(0, 300, n)) * 4.0] * 2)
    out = normalize_master(hot)
    assert float(np.max(np.abs(out))) <= 1.0
