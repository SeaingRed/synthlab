"""Beat repeat (`effects.beat_repeat`, FX unit "beat_repeat"): a stutter
synced to the bpm, fired by a 16th-step pattern and a seeded chance roll.

What it must never do is break the loop: the pattern and the rolls cycle
over `fxrack.repeat_period` (which divides every stem's own loop) and no
repeat runs past its period's end, so the effect on a stem's un-tiled
render, tail folded (`_wrap_tile`), is exactly the effect on the looping
signal. It is offline for the live keys, and transport-bound, so a lane
holding one is never pre-rendered."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio import fxlive
from synthlab.studio.fxrack import (
    FX_UNITS,
    build_chain,
    clean_chain,
    repeat_period,
    resolve_unit,
)
from synthlab.studio.render import (
    RenderInputs,
    _chain_clock,
    _wrap_tile,
    render_audio,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch
from tests.studio.test_loop_assembly import HELD, MID_BAR, PATCH, seam_ratio
from tests.studio.test_song_export import _assemble, _pcm, _three_section_song, _use

BPM = 120.0
SIXTEENTH = int(round(60.0 / BPM / 4.0 * SR))  # 5512.5 -> exact enough here


def unit(pattern=None, **params) -> list[dict]:
    u = {"kind": "beat_repeat", "params": params}
    if pattern is not None:
        u["pattern"] = pattern
    return [u]


def run(x, arr=None, pattern=None, **params):
    arr = arr or Arrangement(bpm=BPM, bars=1, drum_bars=1)
    return fx.chain(x, build_chain(unit(pattern, **params), arr))


def ringing(n: int, seed: int = 3) -> np.ndarray:
    """A decaying tone plus noise: never periodic at any grid length, so a
    repeat is always distinguishable from what it replaced."""
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    x = np.sin(2 * np.pi * 220 * t) * np.exp(-t * 0.8)
    x = np.column_stack([x, np.roll(x, 7)])
    return x + 0.05 * rng.standard_normal((n, 2)) * np.exp(-t)[:, None]


# --- the unit ------------------------------------------------------------------


def test_the_unit_is_registered_with_bounded_params_and_a_pattern():
    spec = FX_UNITS["beat_repeat"]
    assert set(spec["params"]) == {
        "grid",
        "repeats",
        "chance",
        "pitch",
        "decay",
        "gate",
        "mix",
        "seed",
    }
    for lo, hi, default in spec["params"].values():
        assert lo <= default <= hi
    assert spec["pattern"] == "............x..."
    wild = clean_chain(
        [
            {
                "kind": "beat_repeat",
                "params": {"grid": 9, "repeats": 1e9, "chance": -3, "pitch": 7},
                "pattern": "<script>",
            }
        ]
    )[0]
    assert wild["params"]["grid"] == 3.0 and wild["params"]["repeats"] == 16.0
    assert wild["params"]["chance"] == 0.0 and wild["params"]["pitch"] == 0.0
    assert wild["pattern"] == "............x..."


def test_resolve_unit_syncs_to_the_bpm_and_both_loops():
    arr = Arrangement(bpm=128, bars=4, drum_bars=2)
    name, kw = resolve_unit(clean_chain(unit(grid=1.7, repeats=4.9, seed=12.6))[0], arr)
    assert name == "beat_repeat"
    assert kw["bpm"] == 128 and kw["grid"] == 1 and kw["repeats"] == 4
    assert kw["seed"] == 12 and kw["period"] == 8.0  # gcd(4, 2) bars
    assert repeat_period(Arrangement(bars=3, drum_bars=4)) == 4.0
    assert repeat_period(Arrangement(bars=4, drum_bars=4)) == 16.0


def test_mono_in_mono_out_and_stereo_in_stereo_out():
    x = ringing(SR * 2)
    assert run(x).shape == x.shape
    assert run(x[:, 0]).shape == (SR * 2,)


def test_a_repeat_is_the_slice_it_captured():
    """Default: the 13th 16th (beat 4) plays, then its 1/16 slice plays
    three more times in its place. Everything before is untouched."""
    x = ringing(SR * 2)  # one bar at 120 bpm
    y = run(x)
    t0 = int(round(12 * 60.0 / BPM / 4.0 * SR))
    s = int(round(0.25 * 60.0 / BPM * SR))
    assert np.array_equal(y[: t0 + s], x[: t0 + s])
    fade = int(0.0015 * SR)
    for r in (1, 2, 3):
        a = t0 + r * s
        np.testing.assert_allclose(
            y[a + fade : a + s - fade], x[t0 + fade : t0 + s - fade], atol=1e-12
        )
    # and the bar's last 16th is its own again after the repeats end
    assert np.allclose(y[t0 + 4 * s + fade :], x[t0 + 4 * s + fade :])


def test_pitch_decay_and_gate_shape_each_repeat():
    x = np.column_stack([np.sin(2 * np.pi * 440 * np.arange(SR * 2) / SR)] * 2)
    s = int(round(0.25 * 60.0 / BPM * SR))
    t0 = int(round(12 * 60.0 / BPM / 4.0 * SR))
    y = run(x, pitch=-12.0, decay=0.5, gate=0.5, repeats=2)
    first = y[t0 + s : t0 + s + s // 2, 0]
    second = y[t0 + 2 * s : t0 + 2 * s + s // 2, 0]
    spec = np.abs(np.fft.rfft(first * np.hanning(len(first))))
    f = np.fft.rfftfreq(len(first), 1 / SR)
    assert abs(f[np.argmax(spec)] - 220.0) < 15  # an octave down per repeat
    # decay 0.5: the second repeat is half the first, twice over (x0.25 vs x0.5)
    assert np.sqrt((second**2).mean()) == pytest.approx(
        0.5 * np.sqrt((first**2).mean()), rel=0.15
    )
    # gate 0.5: the back half of each repeat is silent
    assert np.max(np.abs(y[t0 + s + s // 2 + 2 : t0 + 2 * s - 2])) < 1e-9


def test_it_is_deterministic_and_the_seed_moves_the_rolls():
    x = ringing(SR * 8)
    kw = {"chance": 0.5, "grid": 3, "seed": 4}
    arr = Arrangement(bpm=BPM, bars=4, drum_bars=4)
    a = run(x, arr, "x.x.x.x.x.x.x.x.", **kw)
    assert np.array_equal(a, run(x, arr, "x.x.x.x.x.x.x.x.", **kw))
    b = run(x, arr, "x.x.x.x.x.x.x.x.", **(kw | {"seed": 5}))
    assert not np.array_equal(a, b)
    # chance 0.5 fires some steps, not all and not none
    full = run(x, arr, "x.x.x.x.x.x.x.x.", **(kw | {"chance": 1.0}))
    assert not np.array_equal(a, x) and not np.array_equal(a, full)
    assert np.array_equal(run(x, arr, chance=0.0), x)


@pytest.mark.parametrize(
    "params,pattern",
    [
        ({}, None),
        ({"grid": 3, "repeats": 16, "pitch": -2.0, "decay": 0.2}, "x..."),
        ({"chance": 0.4, "gate": 0.6, "grid": 1, "seed": 9}, "x.x.x.x.x.x.x.x."),
        ({"grid": 0, "repeats": 2}, "x......."),  # 1/4 slices on the half bar
    ],
)
@pytest.mark.parametrize("bars,drum_bars", [(1, 1), (2, 4), (4, 4)])
def test_the_folded_stem_is_the_effect_on_the_looping_signal(
    params, pattern, bars, drum_bars
):
    """A stem's FX run on its un-tiled render (one loop + its tail) and
    `_wrap_tile` folds the tail on. For the loop to meet itself that must
    equal the effect heard on the loop itself: the steady-state middle of
    three tiled repeats run through it."""
    arr = Arrangement(bpm=BPM, bars=bars, drum_bars=drum_bars)
    own = int(round(bars * 4 * 60.0 / BPM * SR))
    raw = ringing(2 * own)
    chain = build_chain(unit(pattern, **params), arr)
    folded = _wrap_tile(fx.chain(raw, chain), own, own)
    looping = fx.chain(_wrap_tile(raw, own, 3 * own), chain)[own : 2 * own]
    assert not np.allclose(folded, _wrap_tile(raw, own, own))  # it did something
    assert np.max(np.abs(folded - looping)) < 1e-9


def test_a_rendered_loop_meets_itself_and_rerenders_identically():
    fx_units = unit("x.x.x.x.x.x.x.x.", chance=0.6, grid=3, pitch=-1.0)
    for kw in ({"main_fx": fx_units}, {"master_fx": fx_units}):
        inp = RenderInputs(patch=PATCH, arr=HELD, parts=("synth",), **kw)
        a = render_audio(inp)
        assert seam_ratio(a) < 1.5, kw
        assert np.array_equal(a, render_audio(inp)), kw
        assert not np.allclose(
            a, render_audio(RenderInputs(patch=PATCH, arr=HELD, parts=("synth",)))
        )


def test_a_repeat_of_a_reverb_tail_folds_like_the_tail():
    """MID_BAR: one note mid-bar, a long reverb, then the stutter on every
    8th. The head of the loop is the reverb's tail folded round — the
    repeats there must be of *that* tail, not of silence."""
    chain = [{"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5}}]
    chain += unit("x.x.x.x.x.x.x.x.", grid=2)
    a = render_audio(
        RenderInputs(patch=PATCH, arr=MID_BAR, parts=("synth",), main_fx=chain)
    )
    assert float(np.sqrt((a[:8000] ** 2).mean())) > 0.01
    assert seam_ratio(a) < 1.5


def test_its_period_is_in_the_stem_key():
    """A synth stem's own key has no drum bars, yet the period reads them:
    so a beat repeat puts its period in the key — and nothing else does."""
    one, two = Arrangement(bars=4, drum_bars=1), Arrangement(bars=4, drum_bars=2)
    br = clean_chain(unit())
    assert _chain_clock(br, one) != _chain_clock(br, two)
    assert _chain_clock(clean_chain([{"kind": "delay"}]), one) == {}


# --- the song ------------------------------------------------------------------


def test_the_song_export_is_the_plan_with_a_beat_repeat_on_a_track(tmp_path):
    song = _three_section_song()
    song["tracks"][0]["fx"] = song["tracks"][0]["fx"] + unit(
        "x.x.x.x.x.x.x.x.", chance=0.7, grid=3
    )
    with TestClient(create_app(data_dir=tmp_path)) as client:
        _use(client, song, master=unit(grid=2))
        plan = client.get("/song/plan.json").json()
        played = _assemble(client, plan)
        exported = _pcm(client.get("/export/song.wav").content)
        assert exported.shape == played.shape
        assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
        assert np.max(np.abs(exported)) > 1000
        # and the stutter is in it: the same song without it differs
        _use(client, _three_section_song())
        plain = _pcm(client.get("/export/song.wav").content)
        assert np.max(np.abs(plain - exported)) > 100


# --- live keys -----------------------------------------------------------------


def test_live_keys_treat_it_as_offline_and_never_prerender_it(tmp_path):
    assert fxlive.FX_LIVE["beat_repeat"] == "offline"
    assert "beat_repeat" in fxlive.TRANSPORT
    assert fxlive.lane_mode(unit()) == "live"  # like a clock unit's lane
    assert fxlive.lane_mode([{"kind": "granular"}]) == "buffer"
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.svc.state
    # on the edited slot: the keys fall back to the WAV
    s.layers[s.active_layer]["fx"] = unit()
    j = client.get("/patch.json?view=editor").json()
    assert j["worklet_ok"] is False and "fx:beat_repeat" in j["worklet_blockers"]
    # on a follower: its lane stays live (not a pre-rendered one-shot on its
    # own grid) and so blocks the same way
    s.layers[s.active_layer]["fx"] = []
    s.layers.append(
        {
            "patch": Patch(osc1="sine").to_json(),
            "cells": [],
            "follow": {"octave": 0},
            "fx": unit(),
        }
    )
    j = client.get("/patch.json?view=editor").json()
    assert j["layers"][0]["mode"] == "live"
    assert j["worklet_ok"] is False and "fx:beat_repeat" in j["worklet_blockers"]
