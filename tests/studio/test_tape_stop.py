"""Tape stop (`effects.stop`, FX unit "stop"): the last `beats` of every
period slow to a halt — speed and pitch fall in a straight line to zero
over `fall` of that window, then silence to the bar line.

Like the beat repeat it is a time remap, so it is only loop-safe because it
is periodic: its windows restart every `fxrack.repeat_period` (which
divides every stem's own loop) and each reads and writes inside its own
period, so the effect on a stem's un-tiled render, tail folded
(`_wrap_tile`), is exactly the effect on the looping signal. Its clock is
in the stem key. Offline for the live keys, and transport-bound, so a lane
holding one is never pre-rendered and a key is never stopped by it."""

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
from tests.studio.test_beat_repeat import ringing
from tests.studio.test_loop_assembly import HELD, PATCH, seam_ratio
from tests.studio.test_song_export import _assemble, _pcm, _three_section_song, _use

BPM = 120.0
BEAT = int(60.0 / BPM * SR)  # 22050: exact


def unit(**params) -> list[dict]:
    return [{"kind": "stop", "params": params}]


def run(x, arr=None, **params):
    arr = arr or Arrangement(bpm=BPM, bars=1, drum_bars=1)
    return fx.chain(x, build_chain(unit(**params), arr))


def tone(n: int, hz: float = 440.0) -> np.ndarray:
    return np.column_stack([np.sin(2 * np.pi * hz * np.arange(n) / SR)] * 2)


def peak_hz(x: np.ndarray) -> float:
    m = x[:, 0] if x.ndim == 2 else x
    sp = np.abs(np.fft.rfft(m * np.hanning(len(m)), n=1 << 16))
    return float(np.fft.rfftfreq(1 << 16, 1 / SR)[np.argmax(sp)])


# --- the unit ------------------------------------------------------------------


def test_the_unit_is_registered_with_bounded_params_and_no_pattern():
    spec = FX_UNITS["stop"]
    assert spec["label"] == "Tape stop" and "pattern" not in spec
    assert set(spec["params"]) == {"beats", "fall", "mix"}
    for lo, hi, default in spec["params"].values():
        assert lo <= default <= hi
    wild = clean_chain(
        [
            {
                "kind": "stop",
                "params": {"beats": 1e9, "fall": -3, "mix": "x", "speed": 4},
                "pattern": "<script>",
            }
        ]
    )[0]
    assert wild == {"kind": "stop", "params": {"beats": 4.0, "fall": 0.25, "mix": 1.0}}


def test_resolve_unit_syncs_to_the_bpm_and_both_loops():
    arr = Arrangement(bpm=128, bars=4, drum_bars=2)
    name, kw = resolve_unit(clean_chain(unit(beats=2.0, fall=0.5, mix=0.7))[0], arr)
    assert name == "stop"
    assert kw == {"bpm": 128, "beats": 2.0, "fall": 0.5, "mix": 0.7, "period": 8.0}
    assert kw["period"] == repeat_period(arr)


def test_mono_in_mono_out_and_stereo_in_stereo_out():
    x = ringing(SR * 2)
    assert run(x).shape == x.shape
    assert run(x[:, 0]).shape == (SR * 2,)


def test_everything_before_the_window_is_untouched_and_it_enters_in_step():
    """One bar at 120 bpm, the default: the last beat stops. The three
    beats before it are the input bit for bit, and the stop starts at full
    speed, so there is no seam going in: the largest sample step in the
    output is the tone's own."""
    x = tone(4 * BEAT)
    y = run(x)
    assert np.array_equal(y[: 3 * BEAT], x[: 3 * BEAT])
    np.testing.assert_allclose(
        y[3 * BEAT : 3 * BEAT + 8], x[3 * BEAT : 3 * BEAT + 8], atol=1e-3
    )
    assert np.max(np.abs(np.diff(y[:, 0]))) <= np.max(np.abs(np.diff(x[:, 0]))) + 1e-12


def test_the_pitch_falls_in_a_straight_line_to_a_halt():
    """A 440 Hz tone through a one-beat stop: speed 1 - u/F, so a tenth of
    the way in it reads at 0.9 (396 Hz), half way at 0.5 (220 Hz), four
    fifths at 0.2 (88 Hz). Measured in 40 ms windows centred there."""
    x = tone(4 * BEAT)
    y = run(x)
    t0, w = 3 * BEAT, int(0.02 * SR)
    for at, speed in ((0.1, 0.9), (0.3, 0.7), (0.5, 0.5), (0.8, 0.2)):
        c = t0 + int(at * BEAT)
        assert peak_hz(y[c - w : c + w]) == pytest.approx(440.0 * speed, abs=25.0), at
    # ... and it has stopped: under 12 % of full speed the level follows
    # the speed down, so the last 1 % of the beat is as good as silent
    # (the 1.5 ms before the bar line is the dry signal fading back in)
    assert np.max(np.abs(y[4 * BEAT - BEAT // 100 : 4 * BEAT - 80])) < 0.02
    loud = np.sqrt((y[3 * BEAT : 3 * BEAT + BEAT // 2] ** 2).mean())
    assert loud == pytest.approx(np.sqrt(0.5), rel=0.02)  # full level until then


def test_it_reads_only_the_past():
    """Causal: what the stop plays was read from behind the playhead. An
    input that changes only AFTER some point leaves the output before that
    point alone — everywhere, the windows included."""
    rng = np.random.default_rng(2)
    x = rng.uniform(-0.5, 0.5, (8 * BEAT, 2))
    y = run(x, beats=2.0)
    for cut in (int(2.4 * BEAT), int(3.5 * BEAT), int(6.7 * BEAT)):
        z = x.copy()
        z[cut:] = rng.uniform(-0.5, 0.5, (len(x) - cut, 2))
        assert np.array_equal(run(z, beats=2.0)[:cut], y[:cut]), cut


def test_fall_leaves_silence_before_the_bar_line_and_beats_sets_the_window():
    x = tone(4 * BEAT)
    y = run(x, beats=2.0, fall=0.5)  # the fall takes beat 3, beat 4 is silent
    assert np.array_equal(y[: 2 * BEAT], x[: 2 * BEAT])
    assert np.max(np.abs(y[3 * BEAT : 4 * BEAT - 80])) == 0.0
    assert np.sqrt((y[2 * BEAT : 3 * BEAT] ** 2).mean()) > 0.3
    # the dry signal is back for the next bar, over a 1.5 ms fade
    two = run(tone(8 * BEAT), beats=2.0, fall=0.5)
    assert np.array_equal(two[4 * BEAT : 6 * BEAT], tone(8 * BEAT)[4 * BEAT : 6 * BEAT])
    # a window longer than the period is the whole period, never more
    assert np.all(np.isfinite(fx.stop(bpm=BPM, beats=64.0, period=4.0)(x)))


def test_mix_zero_is_the_dry_signal_bit_for_bit_and_mix_blends():
    x = ringing(4 * BEAT)
    assert run(x, mix=0.0) is x  # not even a copy
    mono = x[:, 0]
    assert run(mono, mix=0.0) is mono
    full, half = run(x, mix=1.0), run(x, mix=0.5)
    w = slice(3 * BEAT, 4 * BEAT - 80)
    np.testing.assert_allclose(half[w], 0.5 * x[w] + 0.5 * full[w], atol=1e-12)
    assert np.array_equal(run(x, beats=0.25)[: 3 * BEAT], x[: 3 * BEAT])


def test_it_is_deterministic():
    x = ringing(8 * BEAT)
    assert np.array_equal(run(x, beats=2.0, fall=0.6), run(x, beats=2.0, fall=0.6))


# --- periodic, so it folds ----------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"beats": 2.0, "fall": 0.5},
        {"beats": 0.5, "mix": 0.6},
        {"beats": 4.0},  # the whole bar slows down
        {"beats": 0.25, "fall": 0.25},
    ],
)
@pytest.mark.parametrize("bars,drum_bars", [(1, 1), (2, 4), (4, 4)])
def test_the_folded_stem_is_the_effect_on_the_looping_signal(params, bars, drum_bars):
    """A stem's FX run on its un-tiled render (one loop + its tail) and
    `_wrap_tile` folds the tail on. For the loop to meet itself that must
    equal the effect heard on the loop itself: the steady-state middle of
    three tiled repeats run through it. Measured: 2e-12 at worst."""
    arr = Arrangement(bpm=BPM, bars=bars, drum_bars=drum_bars)
    own = int(round(bars * 4 * 60.0 / BPM * SR))
    raw = ringing(2 * own)
    chain = build_chain(unit(**params), arr)
    folded = _wrap_tile(fx.chain(raw, chain), own, own)
    looping = fx.chain(_wrap_tile(raw, own, 3 * own), chain)[own : 2 * own]
    assert not np.allclose(folded, _wrap_tile(raw, own, own))  # it did something
    assert np.max(np.abs(folded - looping)) < 1e-9


def test_one_period_after_another_is_the_period_alone():
    """The windows restart with the period and never read across it."""
    rng = np.random.default_rng(5)
    bar = 4 * BEAT
    x = rng.uniform(-0.5, 0.5, (bar, 2))
    stop = fx.stop(bpm=BPM, beats=1.5, fall=0.8, period=4.0)
    y = stop(x)
    two = stop(np.vstack([x, x]))
    np.testing.assert_allclose(two[:bar], y, atol=1e-12)
    np.testing.assert_allclose(two[bar:], y, atol=1e-12)
    assert np.max(np.abs(y)) <= np.max(np.abs(x)) + 1e-12  # it only reads the input


def test_a_rendered_loop_meets_itself_and_rerenders_identically():
    fx_units = unit(beats=1.0)
    for kw in ({"main_fx": fx_units}, {"master_fx": fx_units}):
        inp = RenderInputs(patch=PATCH, arr=HELD, parts=("synth",), **kw)
        a = render_audio(inp)
        assert seam_ratio(a) < 1.5, kw
        assert np.array_equal(a, render_audio(inp)), kw
        assert not np.allclose(
            a, render_audio(RenderInputs(patch=PATCH, arr=HELD, parts=("synth",)))
        )


def test_its_period_is_in_the_stem_key_and_no_other_chains_key_moves():
    """A synth stem's own key has no drum bars, yet the period reads them:
    a stop puts its period in the key, as a beat repeat does (the same two
    entries — a chain holding both has them once)."""
    one, two = Arrangement(bars=4, drum_bars=1), Arrangement(bars=4, drum_bars=2)
    st = clean_chain(unit())
    assert _chain_clock(st, one) != _chain_clock(st, two)
    assert _chain_clock(st, one) == {"repeat_period": 4.0, "repeat_bpm": one.bpm}
    br = clean_chain([{"kind": "beat_repeat"}])
    assert _chain_clock(br, one) == _chain_clock(st, one) == _chain_clock(br + st, one)
    assert _chain_clock(clean_chain([{"kind": "delay"}]), one) == {}
    # and the render key follows: the drum bars change what a stop does
    a = RenderInputs(patch=PATCH, arr=one, parts=("synth",), main_fx=st)
    b = RenderInputs(patch=PATCH, arr=two, parts=("synth",), main_fx=st)
    assert a.key() != b.key()
    plain = [{"kind": "delay", "params": {}}]
    a = RenderInputs(patch=PATCH, arr=one, parts=("synth",), main_fx=plain)
    b = RenderInputs(patch=PATCH, arr=two, parts=("synth",), main_fx=plain)
    assert a.key() == b.key()


# --- the song ------------------------------------------------------------------


def test_the_song_export_is_the_plan_with_a_stop_on_a_track(tmp_path):
    song = _three_section_song()
    song["tracks"][0]["fx"] = song["tracks"][0]["fx"] + unit(beats=1.0)
    with TestClient(create_app(data_dir=tmp_path)) as client:
        _use(client, song, master=unit(beats=0.5))
        plan = client.get("/song/plan.json").json()
        played = _assemble(client, plan)
        exported = _pcm(client.get("/export/song.wav").content)
        assert exported.shape == played.shape
        assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
        assert np.max(np.abs(exported)) > 1000
        _use(client, _three_section_song())
        plain = _pcm(client.get("/export/song.wav").content)
        assert np.max(np.abs(plain - exported)) > 100


# --- live keys -----------------------------------------------------------------


def test_live_keys_treat_it_as_offline_and_never_prerender_it(tmp_path):
    assert fxlive.FX_LIVE["stop"] == "offline"
    assert "stop" in fxlive.TRANSPORT
    assert fxlive.lane_mode(unit()) == "live"  # like a clock unit's lane
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.svc.state
    # on the edited slot: the keys fall back to the dry WAV — a held key is
    # never slowed or silenced by where the loop is
    s.layers[s.active_layer]["fx"] = unit()
    j = client.get("/patch.json?view=editor").json()
    assert j["worklet_ok"] is False and "fx:stop" in j["worklet_blockers"]
    dry = client.get("/note/A3.wav")
    s.layers[s.active_layer]["fx"] = []
    assert dry.status_code == 200
    assert dry.content == client.get("/note/A3.wav").content
    # on a follower: its lane stays live and so blocks the same way
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
    assert j["worklet_ok"] is False and "fx:stop" in j["worklet_blockers"]
