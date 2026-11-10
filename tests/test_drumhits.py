"""A drum recording read back into a step pattern (`analysis.drumhits`),
against patterns the studio's own drum machine rendered
(`tests/songs.drum_loop`): the ground truth is the grid that made the audio.

The engine's drums are synthesized, dry and exactly on the grid, so these
numbers are a floor for "is it wired right", not a claim about records
(`tools/lab_measure.py` prints the same table, and what a real stem gave)."""

from __future__ import annotations

import numpy as np
import pytest

from synthlab.analysis import drumhits as dh
from synthlab.core import SR
from synthlab.studio.drumfactory import DRUM_KITS
from tests.songs import drum_loop

PATTERNS = {
    "backbeat": {
        "kick": "x.....x...x.....",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
    },
    "four-floor": {
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "hat": "..x...x...x...x.",
    },
    "breaks": {
        "kick": "x.........x..x..",
        "snare": "....x..x.x..x...",
        "hat": "x.x.x.x.x.x.x.x.",
    },
    "kick-only": {"kick": "x...x...x...x..."},
    "hats-only": {"hat": "x.x.x.x.x.x.x...", "ohat": "..............x."},
    "open-off": {
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "ohat": "..x...x...x...x.",
    },
    "two-bar": {
        "kick": "x.....x...x.....x..x..x...x.....",
        "snare": "....x.......x.......x.......x..x",
        "hat": "x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.",
    },
}
KITS = {"default": {}, **{name: kit["params"] for name, kit in DRUM_KITS.items()}}
# the dirty kits (driven, gated, clanking on purpose) read worse
# and are held to their own floor; the rest keep the bar they were measured at.
# Dubstep (a huge snare) and Synthwave (a gated one) join them:
# their snare tails read as open hats (open hat 0.67 / 0.67, 0.89 / 0.53).
# Cinematic (low toms read as kicks, a long soft snare: kick
# 0.96 / 0.89, snare 0.94 / 0.69), Chiptune (an all-noise snare: snare
# 1.00 / 0.71, open hat 0.50 / 0.73) and Hardstyle (a long driven kick
# whose tail reads as more hits: kick 0.85 / 0.99, open hat 0.64 / 0.47)
# join them too
DIRTY = (
    "Just Ice",
    "Nine Inch Wails",
    "Dubstep",
    "Synthwave",
    "Cinematic",
    "Chiptune",
    "Hardstyle",
)
CLEAN = {k: v for k, v in KITS.items() if k not in DIRTY}
TEMPI = (93, 124, 140)


def _read(rows: dict, bpm: float = 124, **kw) -> tuple[dh.Pattern, list[dh.Hit]]:
    audio, _truth = drum_loop(rows, bpm=bpm, **kw)
    bars = max(len(r) for r in rows.values()) // 16
    return dh.transcribe(audio, 240.0 / bpm, bars)


def _steps(rows: dict, lane: str) -> set[int]:
    return {i for i, ch in enumerate(rows.get(lane, "")) if ch == "x"}


def _found(pat: dh.Pattern, lane: str) -> set[int]:
    return {s for v, s in pat.steps if v == lane}


@pytest.mark.parametrize("name", ["backbeat", "four-floor", "breaks", "two-bar"])
@pytest.mark.parametrize("bpm", TEMPI)
def test_kick_and_snare_come_back_on_their_steps(name, bpm):
    rows = PATTERNS[name]
    pat, _hits = _read(rows, bpm)
    assert _found(pat, "kick") == _steps(rows, "kick")
    # a snare is never missed; "breaks" at speed may gain one
    assert _steps(rows, "snare") <= _found(pat, "snare")
    assert len(_found(pat, "snare") - _steps(rows, "snare")) <= 1
    assert pat.bars == len(rows["kick"]) // 16 and pat.steps_per_bar == 16
    assert pat.row("kick") == rows["kick"]


def _rates(kits) -> dict[str, tuple[float, float]]:
    """(precision, recall) per lane over every pattern x tempo x kit."""
    score = {lane: [0, 0, 0] for lane in ("kick", "snare", "hats", "ohat")}
    for params in kits.values():
        for bpm in TEMPI:
            for rows in PATTERNS.values():
                pat, _hits = _read(rows, bpm, params=params)
                for lane in score:
                    lanes = ("hat", "ohat") if lane == "hats" else (lane,)
                    truth = set().union(*(_steps(rows, v) for v in lanes))
                    got = set().union(*(_found(pat, v) for v in lanes))
                    score[lane][0] += len(truth & got)
                    score[lane][1] += len(got - truth)
                    score[lane][2] += len(truth - got)
    return {
        lane: (tp / max(1, tp + fp), tp / max(1, tp + fn))
        for lane, (tp, fp, fn) in score.items()
    }


def test_precision_and_recall_per_lane_over_kits_tempi_and_patterns():
    """Every pattern x three tempi x the clean kits. Measured when written,
    over three kits (precision / recall): kick 0.991 / 0.996, snare
    0.944 / 1.000, any hat 0.997 / 0.803, open hat 1.000 / 0.778. The hats'
    recall is the stated limit — a hat under a snare is not looked for — and
    the snare's precision is a kick's click read as one at 140 bpm."""
    rates = _rates(CLEAN)
    assert rates["kick"][0] > 0.97 and rates["kick"][1] > 0.97, rates
    assert rates["snare"][0] > 0.93 and rates["snare"][1] > 0.97, rates
    assert rates["hats"][0] > 0.93 and rates["hats"][1] > 0.72, rates
    assert rates["ohat"][0] > 0.9 and rates["ohat"][1] > 0.55, rates


def test_the_dirty_kits_are_read_to_their_own_floor():
    """Just Ice and Nine Inch Wails: a driven, clipped kit, and one of
    gated noise and clanks. Measured when added (precision / recall): kick
    0.94 / 0.95, snare 0.87 / 0.77, any hat 0.91 / 0.79, open hat
    0.54 / 0.67 — a gated snare's tail reads as a hat, a clank as an open
    one. With Dubstep and Synthwave, the four:
    0.95 / 0.97, 0.92 / 0.84, 0.94 / 0.79, 0.62 / 0.63; with
    Cinematic, Chiptune and Hardstyle, the seven: 0.94 / 0.96, 0.89 /
    0.80, 0.94 / 0.79, 0.65 / 0.67 (the clean kits then 1.00 / 1.00,
    0.95 / 0.99, 0.99 / 0.80, 0.94 / 0.61). A pattern read
    from a record this dirty wants a look before it is kept; the floors
    are a little under the measure."""
    rates = _rates({name: KITS[name] for name in DIRTY})
    assert rates["kick"][0] > 0.9 and rates["kick"][1] > 0.9, rates
    assert rates["snare"][0] > 0.82 and rates["snare"][1] > 0.7, rates
    assert rates["hats"][0] > 0.85 and rates["hats"][1] > 0.72, rates
    assert rates["ohat"][0] > 0.45 and rates["ohat"][1] > 0.55, rates


def test_a_hat_on_top_of_a_kick_is_heard_and_one_under_a_snare_is_not():
    rows = PATTERNS["backbeat"]  # hats on every eighth: 0 and 10 with the kick
    pat, hits = _read(rows)
    on_kick = _steps(rows, "kick") & _steps(rows, "hat")
    assert on_kick and on_kick <= _found(pat, "hat")
    assert not _found(pat, "hat") & _steps(rows, "snare")  # the stated limit
    assert {h.lanes for h in hits} >= {("kick", "hat"), ("snare",), ("hat",)}


def test_a_kick_under_a_snare_is_both():
    rows = PATTERNS["four-floor"]  # the snare always lands on a kick
    pat, _hits = _read(rows)
    assert _found(pat, "snare") == {4, 12} and _found(pat, "kick") == {0, 4, 8, 12}


def test_one_drum_alone_is_named_without_another_to_compare_it_to():
    assert _read(PATTERNS["kick-only"])[0].counts == {"kick": 4}
    hats = _read({"hat": "x.x.x.x.x.x.x.x."})[0]
    assert hats.counts == {"hat": 8}


def test_open_and_closed_hats_split_on_how_long_they_ring():
    rows = PATTERNS["hats-only"]
    pat, _hits = _read(rows)
    assert _found(pat, "ohat") == {14} and _found(pat, "hat") == _steps(rows, "hat")
    # nothing to compare with: a ringing hat is still an open one
    assert _read(PATTERNS["open-off"], 93)[0].counts.get("ohat") == 4


def test_swing_is_read_off_the_late_sixteenths_and_taken_out():
    rows = {
        "kick": "x.........x.....",
        "snare": "....x.......x...",
        "hat": ".x...x...x...x.x",
    }
    pat, _hits = _read(rows, 124, swing=0.16)
    assert abs(pat.swing - 0.16) < 0.03
    assert _found(pat, "hat") == _steps(rows, "hat")
    assert _read(rows, 124)[0].swing == 0.0  # the same beat, straight


@pytest.mark.parametrize(
    ("position", "swing", "step"),
    [(3.9, 0.0, 4), (4.4, 0.0, 4), (4.6, 0.0, 5), (5.3, 0.3, 5), (5.9, 0.3, 6)],
)
def test_a_hit_goes_to_the_nearer_of_the_eighth_and_the_swung_sixteenth(
    position, swing, step
):
    assert dh.nearest_step(position, swing) == step


def test_too_few_off_sixteenths_say_nothing_about_swing():
    assert dh.swing_of(np.array([0.0, 2.0, 4.0, 5.2])) == 0.0
    assert dh.swing_of(np.array([1.2, 3.2, 5.2, 7.2])) == 0.2
    assert dh.swing_of(np.array([1.02, 3.0, 5.01, 7.0])) == 0.0


def test_a_ghost_note_is_a_quieter_step_in_the_velocity_lane():
    rows = {"kick": "x.......x.......", "snare": "....x..x....x..."}
    pat, _hits = _read(rows, vel={"7": 0.35})
    assert _found(pat, "snare") == {4, 7, 12}
    assert set(pat.vel) == {"7"} and 0.2 <= pat.vel["7"] <= 0.5


def test_silence_scraps_and_bad_samples_are_no_hits_never_an_error():
    assert dh.find_hits(np.zeros((SR, 2))) == []
    assert dh.find_hits(np.zeros(10)) == []
    assert dh.transcribe(np.zeros(SR), 2.0, 1)[0].steps == []
    assert dh.to_pattern([], 2.0, 1).counts == {}
    audio, _ = drum_loop(PATTERNS["backbeat"])
    audio = audio.copy()
    audio[3000:3010] = np.nan
    audio[9000] = np.inf
    assert dh.transcribe(audio, 240.0 / 120, 1)[0].counts["kick"] == 3
    # a bar length that is no length: nothing on the grid, nothing raised
    assert dh.to_pattern(dh.classify(dh.find_hits(audio)), 0.0, 1).steps == []


def test_the_cut_at_the_end_of_a_region_is_not_a_drum():
    """A region ends mid-ring: the edge is a click in every band."""
    audio, _ = drum_loop(PATTERNS["kick-only"], bpm=124)
    cut = audio[: int(len(audio) * 0.8)]  # mid-way through the last kick's tail
    hits = dh.find_hits(cut)
    assert all(h.time < len(cut) / SR - 0.03 for h in hits)
    assert len(hits) == 4


def test_a_kit_takes_each_drum_from_a_hit_where_it_sounds_alone():
    rows = PATTERNS["backbeat"]
    audio, _ = drum_loop(rows)
    _pat, hits = dh.transcribe(audio, 2.0, 1)
    cuts = dh.exemplars(hits)
    assert set(cuts) == {"snare", "hat"}  # every kick here has a hat on it
    for lane, (a, b) in cuts.items():
        assert 0.05 < b - a <= 0.61 and b <= len(audio) / SR
        source = next(h for h in hits if abs(h.time - a) < 0.01)
        assert source.lanes == (lane,)
    step = 2.0 / 16
    assert round(cuts["snare"][0] / step) in (4, 12)
    lone = dh.exemplars(dh.classify(dh.find_hits(drum_loop(PATTERNS["kick-only"])[0])))
    assert set(lone) == {"kick"}
