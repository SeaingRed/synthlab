"""The per-hit drum model (`synthlab/studio/docs/drums.md`, "The per-hit
model"): velocity, chance, ratchet, nudge and tune a hit, and
a swing a row — against schedules and signals built by hand.

Three things are held here:

- **absent means absent**: a pattern with no per-hit data is scheduled,
  rendered and keyed exactly as before the model existed. The reference is
  a frozen copy of the scheduler it replaced (`_legacy_hits`,
  `_legacy_schedule`), run over every factory beat style, kit, bank
  pattern and saved fixture;
- **each field does one thing**, measured: three onsets inside a ratcheted
  step, an onset moved by the expected samples (and round the loop's seam),
  a doubled pitch, a roll that is the same in another process;
- **one schedule**: the loop, a section's native cycle, a tiled cycle in
  one piece and in chunks, the MIDI file and the sidechain's kick list all
  read the same hits.

Nothing here was listened to.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from synthlab import drums as dr
from synthlab import sequencer
from synthlab.core import SR, n_samples
from synthlab.sequencer import Song
from synthlab.studio import arrange, beatgen, midi_bridge, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.arrange import (
    RATCHET_FALL,
    Hit,
    build_song,
    chance_seed,
    drum_hits,
    hit_roll,
)
from synthlab.studio.clips import save_clip
from synthlab.studio.drumfactory import DRUM_FACTORY, DRUM_KITS
from synthlab.studio.drumlab import specs_of
from synthlab.studio.fxrack import _kick_times, kick_steps
from synthlab.studio.render import (
    RenderInputs,
    render_stems,
    section_audio,
    section_windows,
)
from synthlab.studio.state import DRUM_ROWS, Arrangement, StudioState, drum_row_sounds
from synthlab.synth import Patch

FIXTURES = Path(__file__).parent.parent / "fixtures" / "state_split"
STEP = 60.0 / 120.0 / 4.0  # a sixteenth at 120 bpm, seconds
STEP_N = STEP * SR  # 5512.5 samples


def _arr(drums, hits=None, **kw) -> Arrangement:
    kw.setdefault("bpm", 120.0)
    return Arrangement(
        cells=[], drums=[list(d) for d in drums], drum_hits=dict(hits or {}), **kw
    )


def _events(arr, voice):
    """(start sample, samples, gain) of one voice's placed hits, in time."""
    song = build_song(Patch(), arr, None)
    return sorted(song.tracks[f"drum_{voice}"].events, key=lambda e: e[0])


# --- absent means absent ---------------------------------------------------------


def _legacy_hits(arr):
    """`arrange.drum_hits` as it was before per-hit data (commit 1d91af1),
    verbatim: the reference the new schedule must equal, float for float."""
    steps_per_bar = max(1, int(getattr(arr, "drum_steps", arr.steps_per_bar)))
    total = max(1, int(getattr(arr, "drum_bars", arr.bars))) * steps_per_bar
    step_beats = 4.0 / steps_per_bar
    swing = float(getattr(arr, "drum_swing", 0.0))
    human = float(getattr(arr, "drum_humanize", 0.0))
    mix = getattr(arr, "drum_mix", {}) or {}
    vel = getattr(arr, "drum_vel", {}) or {}
    by_voice: dict[str, list[int]] = {}
    for name, step in arr.drums:
        if name in DRUM_ROWS and 0 <= int(step) < total:
            by_voice.setdefault(name, []).append(int(step))
    seed = zlib.crc32(json.dumps([arr.drums, human, mix], sort_keys=True).encode())
    rng = np.random.default_rng(seed)
    out = []
    for voice, steps in sorted(by_voice.items()):
        m = mix.get(voice, {})
        if not drum_row_sounds(arr, voice):
            continue
        level = float(m.get("level", 1.0))
        prob = float(m.get("prob", 1.0))
        hits = []
        for s in sorted(set(steps)):
            if prob < 1.0 and rng.random() > prob:
                continue
            beat = s * step_beats + (swing * step_beats if s % 2 == 1 else 0.0)
            if human > 0:
                beat += float(rng.normal(0.0, 0.04 * human)) * step_beats
            gain = level * float(vel.get(str(s), 1.0))
            if human > 0:
                gain *= float(np.clip(1.0 + rng.normal(0.0, 0.12 * human), 0.4, 1.3))
            hits.append((max(0.0, beat), gain))
        out.append((voice, hits))
    return out


def _legacy_schedule(arr) -> np.ndarray:
    """The old `_schedule_drums` over the old schedule, rendered."""
    song = Song(bpm=arr.bpm)
    total = arr.drum_bars * arr.drum_steps
    loop_beats = total * (4.0 / arr.drum_steps)
    closed: list[float] = []
    for voice, hits in _legacy_hits(arr):
        valid = {spec["name"] for spec in specs_of(voice)}
        params = {
            k: v for k, v in (arr.drum_params.get(voice) or {}).items() if k in valid
        }
        sample = dr.hit(voice, **params)
        track = f"drum_{voice}"
        song.track(track)
        for beat, gain in hits:
            hit = sample
            if voice == "hat":
                closed.append(beat)
            if voice == "ohat" and closed:
                nxt = min((c for c in closed if c > beat), default=None)
                if nxt is None:
                    nxt = min(closed) + loop_beats
                n = n_samples((nxt - beat) * 60.0 / arr.bpm, SR)
                if n < len(sample):
                    hit = sample[:n].copy()
                    fade = min(n, n_samples(0.008, SR))
                    hit[-fade:] *= (
                        np.linspace(1.0, 0.0, fade)[:, None]
                        if hit.ndim == 2
                        else np.linspace(1.0, 0.0, fade)
                    )
            song.play(track, hit, beat, gain)
    return song.render(normalize=0)


def _factory_arrangements():
    for style in beatgen.STYLES:
        for seed in (1, 7, 4127):
            for bars, steps in ((1, 16), (2, 16), (1, 8), (2, 32)):
                yield (
                    f"{style}/{seed}/{bars}x{steps}",
                    Arrangement(
                        cells=[],
                        drums=beatgen.generate(style, seed, bars, steps),
                        drum_bars=bars,
                        drum_steps=steps,
                        drum_swing=beatgen.STYLE_SWING.get(style, 0.0),
                    ),
                )
    for name, kit in DRUM_KITS.items():
        p = kit["pattern"]
        yield (
            f"kit/{name}",
            Arrangement(
                cells=[],
                drums=[list(s) for s in p["steps"]],
                drum_bars=p["bars"],
                drum_steps=p["steps_per_bar"],
                drum_swing=p["swing"],
                drum_vel=dict(kit["vel"]),
                drum_params={v: dict(x) for v, x in kit["params"].items()},
            ),
        )
    for p in DRUM_FACTORY:
        yield (
            f"bank/{p['name']}",
            Arrangement(
                cells=[],
                drums=[list(s) for s in p["steps"]],
                drum_bars=p["bars"],
                drum_steps=p["steps_per_bar"],
                drum_swing=p.get("swing", 0.0),
            ),
        )
    for f in sorted(FIXTURES.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "arrangement" in d:
            yield (
                f"fixture/{f.name}",
                replace(
                    Arrangement(
                        **schema.conform(d["arrangement"], schema.nodes().ARRANGEMENT)
                    ),
                    cells=[],
                ),
            )


FACTORY = list(_factory_arrangements())


def test_the_factory_set_is_every_style_kit_slot_and_fixture():
    names = [n for n, _a in FACTORY]
    assert len(names) == len(beatgen.STYLES) * 12 + len(DRUM_KITS) + len(
        DRUM_FACTORY
    ) + len([f for f in FIXTURES.glob("*.json") if f.name != "surface.json"])
    assert all(not a.drum_hits and not a.drum_row_swing for _n, a in FACTORY)


@pytest.mark.parametrize("name,arr", FACTORY, ids=[n for n, _a in FACTORY])
def test_no_per_hit_data_is_the_old_schedule_float_for_float(name, arr):
    """The schedule, and under the mixer's probability, humanise and a
    velocity lane too (the seeded stream is drawn exactly as it was)."""
    assert drum_hits(arr) == _legacy_hits(arr)
    rough = replace(
        arr,
        drum_humanize=0.5,
        drum_vel={"3": 0.4, "17": 0.2},
        drum_mix={"hat": {"prob": 0.6, "level": 0.8}, "kick": {"prob": 0.9}},
    )
    assert drum_hits(rough) == _legacy_hits(rough)
    assert all(
        type(h) is Hit and h.tune == 0.0 and h.until is None
        for _v, hs in drum_hits(rough)
        for h in hs
    )
    assert kick_steps(arr) == [
        s for v, s in arr.drums if v == "kick" and s < arr.drum_bars * arr.drum_steps
    ]


@pytest.mark.parametrize(
    "name,arr",
    [c for c in FACTORY if c[0].split("/")[-1] in ("1x16",) or "/" not in c[0][5:]],
    ids=lambda c: c if isinstance(c, str) else "",
)
def test_no_per_hit_data_is_the_old_render_byte_for_byte(name, arr):
    """One bar of every style and seed, every kit, bank slot and fixture:
    the samples the old scheduler placed."""
    new = build_song(Patch(), arr, None).render(normalize=0)
    np.testing.assert_array_equal(new, _legacy_schedule(arr))


def test_an_unset_table_is_in_no_key_no_payload_and_no_state_byte(tmp_path):
    """`drum_hits` / `drum_row_swing` are Arrangement fields and are in
    NOTHING that is written down while they are empty: the loop key, a
    stem key, the session file, a baseline, a pool pattern."""
    arr = _arr([["kick", 0], ["hat", 2]])
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("synth", "drums"))
    assert "drum_hits" not in schema.arr_dict(arr)
    assert "drum_row_swing" not in schema.arr_dict(arr)
    (job,) = [j for j in render._stem_jobs(inp) if j.kind == "drums"]
    assert not {"drum_hits", "row_swing"} & set(job.cache_payload())
    s = StudioState(tmp_path)
    payload = s._payload()
    assert not set(schema.ARR_OPTIONAL) & set(payload["arrangement"])
    assert "arr.drum_hits" not in payload["baselines"]["pattern"]["data"]
    assert all("hits" not in p for p in payload["drum_bank"] if p)
    assert "drum_hits" not in schema.nodes().ARRANGEMENT.default()
    # and it is in all of them once set
    with_hit = replace(
        arr, drum_hits={"hat.2": {"p": 0.5}}, drum_row_swing={"hat": 0.2}
    )
    keyed = RenderInputs(patch=Patch(), arr=with_hit, parts=("synth", "drums"))
    assert keyed.key() != inp.key()
    (job2,) = [j for j in render._stem_jobs(keyed) if j.kind == "drums"]
    assert job2.cache_payload()["drum_hits"] == {"hat.2": {"p": 0.5}}
    assert job2.cache_payload()["row_swing"] == {"hat": 0.2}
    # ... and never in a render that plays no drums
    quiet = RenderInputs(patch=Patch(), arr=with_hit, parts=("synth",))
    assert quiet.key() == RenderInputs(patch=Patch(), arr=arr, parts=("synth",)).key()
    s.arrangement = with_hit
    s.mark_baseline("pattern", "x")
    saved = s._payload()
    assert saved["arrangement"]["drum_hits"] == {"hat.2": {"p": 0.5}}
    assert saved["baselines"]["pattern"]["data"]["arr.drum_hits"] == {
        "hat.2": {"p": 0.5}
    }


def test_a_baseline_from_before_the_table_reverts_it_to_unset(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement = _arr([["kick", 0]])
    s.mark_baseline("pattern", "then")
    assert "arr.drum_hits" not in s.baselines["pattern"]["data"]
    s.arrangement = replace(
        s.arrangement, drum_hits={"kick.0": {"r": 3}}, drum_row_swing={"kick": 0.1}
    )
    assert s.is_dirty("pattern")
    assert s.revert("pattern")
    assert s.arrangement.drum_hits == {} and s.arrangement.drum_row_swing == {}
    assert not s.is_dirty("pattern")


def test_values_that_say_nothing_are_not_stored_and_a_hit_needs_its_cell():
    n = schema.nodes()
    raw = {
        "kick.0": {"p": 1.0, "r": 1, "n": 0.0, "t": 0.0},  # says nothing
        "kick.4": {"v": 1.0, "p": 1.0},  # a velocity always says something
        "hat.2": {"r": 9, "n": -3, "t": 99, "p": -1, "v": 0},  # clamped
        "hat.6": {"r": 2},  # no cell
        "cowbell.0": {"r": 2},
        "kick.999": {"r": 2},
        "kick.x": {"r": 2},
        "kick": {"r": 2},
        "kick.8": "loud",
        "kick.12": {"r": "3", "bogus": 1},
    }
    assert schema.conform(raw, n.DRUM_HITS) == {
        "kick.4": {"v": 1.0},
        "hat.2": {"v": 0.05, "p": 0.0, "r": 4, "n": -0.5, "t": 12.0},
        "hat.6": {"r": 2},
    }
    cells = [["kick", 0], ["kick", 4], ["hat", 2]]
    assert arrange.clean_hits(raw, cells) == {
        "kick.4": {"v": 1.0},
        "hat.2": {"v": 0.05, "p": 0.0, "r": 4, "n": -0.5, "t": 12.0},
    }
    arr = schema.conform({"drums": cells, "drum_hits": raw}, n.ARRANGEMENT)
    assert set(arr["drum_hits"]) == {"kick.4", "hat.2"}
    for hostile in (None, 5, "x", [1], {"kick.0": None}, {1: 2}):
        assert arrange.clean_hits(hostile, cells) == {}
        assert "drum_hits" not in schema.conform(
            {"drums": cells, "drum_hits": hostile}, n.ARRANGEMENT
        )
    assert schema.conform(
        {"kick": 9, "hat": -1, "moo": 0.2, "rim": "x"}, n.ROW_SWING
    ) == {
        "kick": 0.6,
        "hat": 0.0,
    }


# --- each field, measured ---------------------------------------------------------


def test_a_velocity_on_a_hit_replaces_the_steps_for_that_voice_alone():
    """A kick and a ghost snare on one step: the step's lane holds one
    value for both, a hit's `v` its own."""
    arr = _arr(
        [["kick", 4], ["snare", 4]],
        {"snare.4": {"v": 0.3}},
        drum_vel={"4": 0.8},
        drum_mix={"snare": {"level": 0.5}},
    )
    hits = dict(drum_hits(arr))
    assert hits["kick"] == [(1.0, 0.8)]
    assert hits["snare"] == [(1.0, 0.5 * 0.3)]


def test_a_ratchet_of_three_is_three_onsets_a_third_of_a_step_apart():
    """Three hits inside the step, evenly, each 0.85 of the one before —
    and each cut by the next (a voice struck again, not three ringing)."""
    arr = _arr([["tom", 4]], {"tom.4": {"r": 3}}, drum_vel={"4": 0.8})
    (hits,) = [h for v, h in drum_hits(arr) if v == "tom"]
    assert [round(b, 9) for b, _g in hits] == [
        1.0,
        round(1 + 1 / 12, 9),
        round(1 + 2 / 12, 9),
    ]
    assert [g for _b, g in hits] == pytest.approx([0.8, 0.8 * 0.85, 0.8 * 0.85**2])
    assert RATCHET_FALL == 0.85
    assert [h.until for h in hits] == [hits[1][0], hits[2][0], None]
    ev = _events(arr, "tom")
    starts = [e[0] for e in ev]
    assert starts == [round((4 + j / 3) * STEP_N) for j in range(3)]
    sub = round(STEP_N / 3)
    whole = len(dr.hit("tom"))
    assert whole > 3 * sub  # a tom rings far longer than a third of a step
    assert [len(e[1]) for e in ev] == [sub, sub, whole] or [
        abs(len(e[1]) - sub) <= 1 for e in ev[:2]
    ] == [True, True]
    assert all(np.max(np.abs(e[1][-1])) < 1e-9 for e in ev[:2])  # faded out at the cut
    # in the audio: three attacks, and nothing of hit 1 left under hit 3
    x = build_song(Patch(), arr, None).render(normalize=0)[:, 0]
    one = build_song(Patch(), _arr([["tom", 4]], drum_vel={"4": 0.8}), None).render(
        normalize=0
    )[:, 0]
    for j in range(3):
        at = starts[j]
        assert np.max(np.abs(x[at : at + 400])) > 0.1
    third = starts[2]
    np.testing.assert_allclose(
        x[third : third + 2000], one[starts[0] : starts[0] + 2000] * 0.85**2, atol=1e-9
    )
    assert drum_hits(_arr([["tom", 4]], {"tom.4": {"r": 4}}))[0][1][3][0] == 1 + 3 / 16


def test_a_ratcheted_open_hat_is_still_choked_by_the_closed_one():
    arr = _arr([["ohat", 0], ["hat", 1]], {"ohat.0": {"r": 2}})
    ev = _events(arr, "ohat")
    assert [e[0] for e in ev] == [0, round(STEP_N / 2)]
    # the first is cut by its own retrigger, the second by the closed hat
    assert abs(len(ev[0][1]) - round(STEP_N / 2)) <= 1
    assert abs(len(ev[1][1]) - round(STEP_N / 2)) <= 1


def test_a_nudge_moves_the_onset_by_its_share_of_a_step():
    for n in (0.5, 0.25, -0.25, -0.5):
        arr = _arr([["rim", 4], ["kick", 0]], {"rim.4": {"n": n}})
        (ev,) = _events(arr, "rim")
        assert ev[0] == round((4 + n) * STEP_N)
    # the other hits do not move
    arr = _arr([["rim", 4], ["rim", 8]], {"rim.4": {"n": 0.5}})
    assert [e[0] for e in _events(arr, "rim")] == [
        round(4.5 * STEP_N),
        round(8 * STEP_N),
    ]
    # before swing: an odd step is nudged, then swung
    arr = _arr([["rim", 3]], {"rim.3": {"n": -0.25}}, drum_swing=0.2)
    assert _events(arr, "rim")[0][0] == round((3 - 0.25 + 0.2) * STEP_N)


def test_a_hit_nudged_before_the_loops_first_beat_plays_at_its_end():
    """The loop is a circle: step 0 a quarter step early is the last
    quarter step of the loop — in the schedule, in the loop's wrapped
    stem, and in a section's cycle and tail."""
    arr = _arr([["rim", 0]], {"rim.0": {"n": -0.25}})
    (hit,) = drum_hits(arr)[0][1]
    assert hit[0] == pytest.approx(4.0 - 0.25 * 0.25)
    loop = n_samples(2.0, SR)
    at = round(15.75 * STEP_N)
    # the loop's stem: silent until the hit, which rings round the seam
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("drums",))
    ((_name, stem),) = render_stems(inp)
    assert len(stem) == loop
    assert np.max(np.abs(stem[at - 2000 : at - 1])) == 0.0
    assert np.max(np.abs(stem[at : at + 300])) > 0.1
    rim = dr.hit("rim")
    spill = at + len(rim) - loop
    assert spill > 500  # it does cross the seam
    np.testing.assert_allclose(stem[:spill], rim[len(rim) - spill :], atol=1e-12)
    np.testing.assert_allclose(stem[at:], rim[: loop - at], atol=1e-12)
    # the same hit on the grid's last step, nudged late by 0.75: the same samples
    late = _arr([["rim", 15]], {"rim.15": {"n": 0.5}}, drum_steps=16)
    assert drum_hits(late)[0][1][0][0] == pytest.approx(15.5 * 0.25)
    # a section: the cycle holds the attack at its end, the tail the rest
    song, sid = _section([["rim", 0]], {"rim.0": {"n": -0.25}}, bars=1)
    audio = section_audio(sm.section_arrangement(song, song["sections"][0]))
    np.testing.assert_allclose(audio.base[at:], rim[: loop - at], atol=1e-12)
    np.testing.assert_allclose(audio.tail[:spill], rim[len(rim) - spill :], atol=1e-12)
    assert np.max(np.abs(audio.base[:at])) == 0.0


def _pitch(x: np.ndarray, lo=0.12, hi=0.3) -> float:
    x = x.mean(axis=1) if x.ndim == 2 else x
    seg = x[int(lo * SR) : int(hi * SR)] * np.hanning(int(hi * SR) - int(lo * SR))
    spec = np.abs(np.fft.rfft(seg, 1 << 17))
    return float(np.argmax(spec) * SR / (1 << 17))


def test_a_tune_shifts_the_measured_pitch_of_that_hit():
    """A tom an octave up is twice the frequency; seven semitones up is
    2^(7/12); the hit beside it keeps the voice's own pitch."""
    arr = _arr([["tom", 0], ["tom", 8]], {"tom.8": {"t": 12}})
    a, b = _events(arr, "tom")
    base = _pitch(a[1])
    assert base == pytest.approx(140.0, rel=0.03)
    assert _pitch(b[1]) == pytest.approx(2 * base, rel=0.02)
    arr = _arr([["tom", 0]], {"tom.0": {"t": 7}})
    assert _pitch(_events(arr, "tom")[0][1]) == pytest.approx(
        base * 2 ** (7 / 12), rel=0.02
    )
    arr = _arr(
        [["tom", 0]], {"tom.0": {"t": -12}}, drum_params={"tom": {"tune": 200.0}}
    )
    assert _pitch(_events(arr, "tom")[0][1]) == pytest.approx(100.0, rel=0.03)
    # a voice with no tune of its own is itself, whatever the hit says
    arr = _arr([["clap", 0]], {"clap.0": {"t": 12}})
    np.testing.assert_array_equal(_events(arr, "clap")[0][1], dr.hit("clap"))
    assert [v for v in DRUM_ROWS if any(s["name"] == "tune" for s in specs_of(v))] == [
        "kick",
        "snare",
        "tom",
        "rim",
    ]


def test_a_tune_on_a_sampled_row_resamples_the_clip(tmp_path):
    t = np.arange(int(0.4 * SR)) / SR
    save_clip(tmp_path, "tone", np.sin(2 * np.pi * 220.0 * t) * 0.5, SR)
    arr = _arr(
        [["clap", 0], ["clap", 8]],
        {"clap.8": {"t": 12}},
        drum_samples={"clap": "tone"},
    )
    song = build_song(Patch(), arr, None, clip_root=tmp_path)
    a, b = sorted(song.tracks["drum_clap"].events, key=lambda e: e[0])
    assert _pitch(a[1], 0.05, 0.3) == pytest.approx(220.0, rel=0.01)
    assert _pitch(b[1], 0.02, 0.18) == pytest.approx(440.0, rel=0.01)
    assert len(b[1]) == pytest.approx(len(a[1]) / 2, abs=2)


def test_a_rows_swing_moves_only_that_rows_off_steps():
    cells = [[v, s] for v in ("hat", "rim") for s in range(4)]
    plain = dict(drum_hits(_arr(cells, drum_swing=0.1)))
    own = dict(drum_hits(_arr(cells, drum_swing=0.1, drum_row_swing={"hat": 0.4})))
    assert own["rim"] == plain["rim"]
    assert [b for b, _g in own["hat"]] == pytest.approx(
        [0.0, 0.25 + 0.4 * 0.25, 0.5, 0.75 + 0.4 * 0.25]
    )
    # a row may be straight while the kit swings
    straight = dict(drum_hits(_arr(cells, drum_swing=0.3, drum_row_swing={"rim": 0.0})))
    assert [b for b, _g in straight["rim"]] == [0.0, 0.25, 0.5, 0.75]
    assert [b for b, _g in straight["hat"]] == [b for b, _g in plain["hat"]][:0] + [
        0.0,
        0.25 + 0.3 * 0.25,
        0.5,
        0.75 + 0.3 * 0.25,
    ]


# --- chance ------------------------------------------------------------------------


def _chancy(p=0.5, steps=32, bars=4) -> Arrangement:
    cells = [["hat", s] for s in range(bars * steps)] + [
        ["kick", s] for s in range(0, bars * steps, 4)
    ]
    return _arr(
        cells,
        {f"hat.{s}": {"p": p} for s in range(bars * steps)},
        drum_bars=bars,
        drum_steps=steps,
    )


def test_a_chance_of_zero_never_sounds_and_of_one_always():
    assert dict(drum_hits(_chancy(0.0)))["hat"] == []
    assert len(dict(drum_hits(_chancy(1.0)))["hat"]) == 128
    played = len(dict(drum_hits(_chancy(0.5)))["hat"])
    assert 44 <= played <= 84  # 128 fair rolls: 64 +- 20
    quarter = len(dict(drum_hits(_chancy(0.25)))["hat"])
    assert 14 <= quarter <= 50
    assert len(dict(drum_hits(_chancy(0.5)))["kick"]) == 32  # the kicks are certain


def test_a_chance_times_the_voices_own():
    arr = replace(_chancy(0.5), drum_mix={"hat": {"prob": 0.5}})
    played = len(dict(drum_hits(arr))["hat"])
    assert 14 <= played <= 50  # 128 x 0.25


def test_the_roll_is_the_same_every_time_and_in_another_process():
    """A hash of the row's cells and the hit's step — never `hash()`
    (salted per process), never a stream."""
    arr = _chancy(0.5)
    mine = [b for b, _g in dict(drum_hits(arr))["hat"]]
    assert mine == [b for b, _g in dict(drum_hits(arr))["hat"]]
    code = (
        "import json;"
        "from synthlab.studio.arrange import drum_hits;"
        "from synthlab.studio.state import Arrangement;"
        "cells=[['hat',s] for s in range(128)]+[['kick',s] for s in range(0,128,4)];"
        "a=Arrangement(cells=[],bpm=120.0,drums=cells,drum_bars=4,drum_steps=32,"
        "drum_hits={f'hat.{s}':{'p':0.5} for s in range(128)});"
        "print(json.dumps([b for b,_g in dict(drum_hits(a))['hat']]))"
    )
    for hashseed in ("0", "4242"):
        got = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": hashseed},
        )
        assert json.loads(got.stdout) == mine
    assert hit_roll(1, 2) == hit_roll(1, 2) != hit_roll(1, 3)
    assert 0.0 <= hit_roll(123, 7) < 1.0
    assert chance_seed("hat", [3, 1, 1]) == chance_seed("hat", (1, 3))


def test_a_rows_rolls_are_its_own():
    """Another row's edit, a velocity, a nudge or the chance itself does
    not move which of this row's hits a roll keeps; a hit added to the row
    rolls it again."""
    base = _chancy(0.5, steps=16, bars=2)
    keep = [b for b, _g in dict(drum_hits(base))["hat"]]
    other = replace(base, drums=[*base.drums, ["rim", 3], ["clap", 4]])
    assert [b for b, _g in dict(drum_hits(other))["hat"]] == keep
    louder = replace(
        base, drum_hits={k: {**h, "v": 0.3} for k, h in base.drum_hits.items()}
    )
    assert [b for b, _g in dict(drum_hits(louder))["hat"]] == keep
    # a hit that plays at 0.5 plays at any higher chance
    surer = replace(base, drum_hits={k: {"p": 0.8} for k in base.drum_hits})
    assert set(keep) <= {b for b, _g in dict(drum_hits(surer))["hat"]}
    fewer = replace(base, drums=[c for c in base.drums if c != ["hat", 5]])
    assert [b for b, _g in dict(drum_hits(fewer))["hat"]] != [
        b for b in keep if b != 1.25
    ]


def test_a_chance_never_re_humanises_the_hits_after_it():
    """The humanise draws are made whatever the roll says."""
    cells = [["hat", s] for s in range(16)]
    plain = _arr(cells, drum_humanize=0.6)
    rolled = replace(plain, drum_hits={"hat.0": {"p": 0.0}})
    assert dict(drum_hits(rolled))["hat"] == dict(drum_hits(plain))["hat"][1:]


# --- one schedule: loop == section == chunks == export == sidechain -----------------

BEAT = {
    "drums": [
        ["kick", 0],
        ["kick", 8],
        ["hat", 0],
        ["hat", 4],
        ["hat", 6],
        ["hat", 10],
        ["hat", 14],
        ["rim", 3],
        ["rim", 11],
        ["tom", 12],
        ["snare", 4],
    ],
    "hits": {
        "hat.0": {"n": -0.25, "v": 0.5},
        "hat.4": {"p": 0.5},
        "hat.6": {"p": 0.5, "r": 2},
        "hat.10": {"p": 0.5},
        "hat.14": {"p": 0.5, "n": 0.25},
        "rim.3": {"p": 0.4},
        "rim.11": {"p": 0.6, "t": 5},
        "tom.12": {"r": 3, "t": -4},
        "kick.8": {"n": -0.5},
    },
    "swing": {"hat": 0.2},
}


def _section(drums, hits, bars=4, pattern_bars=1, swing=None, bpm=120.0, lane=False):
    song = sm.new_song(bpm, "A1", "minor")
    tid = sm.add_track(song, "drums", "Drums")
    sid = sm.add_section(song, "S", bars)
    data = {"drums": drums, "bars": pattern_bars, "steps": 16, "hits": hits}
    if swing:
        data["swing"] = swing
    sm.set_cell(song, sid, tid, sm.add_pattern(song, "drums", "beat", data))
    if lane:  # a level lane makes the cycle one flat pass of the section (a
        # fader after the stem: 0.4 of unity, every chunk alike)
        sm.set_lane(song, sid, tid, "gain", [[0.0, 0.2], [1.0, 0.2]])
    return song, sid


def _loop_beats(arr, reps) -> dict:
    length = arr.drum_bars * 4.0
    return {
        v: sorted(round(b + r * length, 9) for r in range(reps) for b, _g in hs)
        for v, hs in drum_hits(arr)
    }


def _window_beats(parts) -> dict:
    out: dict = {}
    for b0, b1 in section_windows(parts):
        for v, hs in drum_hits(parts.window(b0, b1).arr):
            out.setdefault(v, []).extend(round(b + b0 * 4.0, 9) for b, _g in hs)
    return {v: sorted(x) for v, x in out.items()}


def test_a_pattern_rolls_the_same_looping_in_a_section_and_in_every_chunk(monkeypatch):
    """The loop on Perform, the section playing it natively, the section
    tiled onto one flat cycle, and that cycle cut into chunks: one set of
    hits, repeat after repeat."""
    loop = _arr(BEAT["drums"], BEAT["hits"], drum_row_swing=BEAT["swing"])
    want = _loop_beats(loop, 4)
    assert 0 < len(want["hat"]) < 4 * 6  # some rolled away, some kept
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"])
    native = sm.section_arrangement(song, song["sections"][0])
    assert not native.flat
    assert native.arr.drum_hits == loop.drum_hits and native.arr.drum_row_swing == {
        "hat": 0.2
    }
    assert _loop_beats(native.arr, 4) == want
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"], lane=True)
    flat = sm.section_arrangement(song, song["sections"][0])
    assert flat.flat and flat.arr.drum_bars == 4
    whole = {v: sorted(round(b, 9) for b, _g in hs) for v, hs in drum_hits(flat.arr)}
    assert whole == want
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 2.0)
    assert section_windows(flat) == [(0, 1), (1, 2), (2, 3), (3, 4)]
    assert _window_beats(flat) == want
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 6.0)
    assert section_windows(flat) == [(0, 3), (3, 4)]
    assert _window_beats(flat) == want


@pytest.mark.parametrize("cap", [2.0, 4.0, 6.0], ids=["4 chunks", "2 chunks", "3+1"])
def test_a_cycle_with_per_hit_data_in_chunks_is_the_cycle_in_one_piece(
    monkeypatch, cap
):
    """Audio, to 1e-9: every field at once, a hit nudged early on each
    chunk's first step (it sounds in the chunk before), the cycle's own
    first step nudged early (it sounds at the cycle's end, and rings into
    the tail), a ratchet across the last step."""
    hits = {**BEAT["hits"], "kick.0": {"n": -0.3}, "tom.12": {"r": 3, "t": -4}}
    drums = [*BEAT["drums"], ["snare", 15]]
    hits["snare.15"] = {"r": 4, "n": 0.4}
    # 125 bpm: a bar, a step and a third of a step are whole samples, so a
    # chunk's offset plus a hit's place is the hit's place in the cycle
    song, sid = _section(drums, hits, swing=BEAT["swing"], lane=True, bpm=125.0)
    parts = sm.section_arrangement(song, song["sections"][0])
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 100.0)
    assert len(section_windows(parts)) == 1
    whole = section_audio(parts)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", cap)
    assert len(section_windows(parts)) > 1
    chunked = section_audio(parts)
    assert np.max(np.abs(whole.base)) > 0.1
    np.testing.assert_allclose(chunked.base, whole.base, atol=1e-9)
    m = max(len(chunked.tail), len(whole.tail))
    pad = lambda t: np.vstack([t, np.zeros((m - len(t), 2))])  # noqa: E731
    np.testing.assert_allclose(pad(chunked.tail), pad(whole.tail), atol=1e-9)
    # the early kick of bar 1 is at the END of the cycle, not its start
    n = len(whole.base)
    at = n - round(0.3 * 5292)
    assert np.max(np.abs(whole.base[at : at + 300])) > 0.1


def test_a_native_section_is_its_loop_wrap_tiled(monkeypatch):
    """A section that plays its beat natively renders the loop's own
    render: its cycle with its tail folded onto the start is the loop's
    wrap-tiled stem — the early hat of step 0 at the end, ringing round."""
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"], bars=2)
    parts = sm.section_arrangement(song, song["sections"][0])
    assert not parts.flat and parts.cycle_bars == 1
    audio = section_audio(parts)
    loop = _arr(BEAT["drums"], BEAT["hits"], drum_row_swing=BEAT["swing"])
    ((_n, stem),) = render_stems(
        RenderInputs(patch=Patch(), arr=loop, parts=("drums",))
    )
    folded = audio.base.copy()
    folded[: len(audio.tail)] += audio.tail
    np.testing.assert_allclose(folded, stem, atol=1e-9)
    assert np.max(np.abs(audio.tail)) > 0.01


def _serial(monkeypatch):
    monkeypatch.setattr(render, "_each", lambda fn, items: [fn(x) for x in items])
    thunks = lambda ts: [t() for t in ts]  # noqa: E731
    monkeypatch.setattr(sequencer, "render_each", thunks)
    monkeypatch.setattr(arrange, "render_each", thunks)


def test_parallel_is_serial_with_per_hit_data(monkeypatch):
    arr = replace(
        _arr(BEAT["drums"], BEAT["hits"], drum_row_swing=BEAT["swing"]),
        cells=[["A1", 0], ["A1", 2]],
        drum_humanize=0.3,
    )
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("synth", "drums"))
    parallel = render.render_audio(inp)
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"], lane=True)
    parts = sm.section_arrangement(song, song["sections"][0])
    sec = section_audio(parts)
    with monkeypatch.context() as m:
        _serial(m)
        np.testing.assert_array_equal(parallel, render.render_audio(inp))
        np.testing.assert_array_equal(sec.base, section_audio(parts).base)


def test_the_midi_file_holds_the_hits_the_audio_plays():
    """Ratchets as repeated notes (each ended where the next begins), a
    nudge as the note's own time, a chance as the rolled result."""
    arr = _arr(BEAT["drums"], BEAT["hits"], drum_row_swing=BEAT["swing"])
    file = midi_bridge.loop_file(RenderInputs(patch=Patch(), arr=arr, parts=("drums",)))
    (track,) = file.tracks
    got = sorted((round(n.start, 9), n.pitch) for n in track.notes)
    want = sorted(
        (round(b, 9), midi_bridge.GM_DRUMS[v])
        for v, hs in drum_hits(arr)
        for b, _g in hs
    )
    assert got == want
    toms = sorted((n.start, n.length) for n in track.notes if n.pitch == 45)
    assert [round(s, 9) for s, _l in toms] == [
        3.0,
        round(3 + 1 / 12, 9),
        round(3 + 2 / 12, 9),
    ]
    assert [round(length, 9) for _s, length in toms] == [
        round(1 / 12, 9),
        round(1 / 12, 9),
        0.25,
    ]
    early = [n for n in track.notes if n.pitch == 42 and n.start > 3.9]
    assert len(early) == 1 and early[0].start == pytest.approx(4 - 0.25 * 0.25)
    assert early[0].velocity == round(0.5 * 127)
    # the song's file: the same hits a pass, pass after pass
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"])
    sfile = midi_bridge.song_file(song)
    (strack,) = sfile.tracks
    assert sorted((round(n.start, 9), n.pitch) for n in strack.notes) == sorted(
        (round(b + r * 4.0, 9), p) for r in range(4) for b, p in got
    )
    song, sid = _section(BEAT["drums"], BEAT["hits"], swing=BEAT["swing"], lane=True)
    (ftrack,) = midi_bridge.song_file(song).tracks
    assert sorted((round(n.start, 9), n.pitch) for n in ftrack.notes) == sorted(
        (round(b + r * 4.0, 9), p) for r in range(4) for b, p in got
    )


def test_the_sidechain_ducks_where_a_kick_with_per_hit_data_lands():
    """A chance is not followed (0 is: the hit never plays); a nudge and a
    ratchet are — they are facts of the pattern, not rolls; swing is not,
    as before. Plain kicks stay whole numbers."""
    cells = [["kick", 0], ["kick", 4], ["kick", 8], ["kick", 12], ["kick", 14]]
    arr = _arr(
        cells,
        {
            "kick.0": {"n": -0.25},
            "kick.4": {"r": 2},
            "kick.8": {"p": 0.5, "n": 0.5},
            "kick.12": {"p": 0.0},
            "kick.14": {"v": 0.3, "t": 3},
        },
        drum_row_swing={"kick": 0.4},
    )
    assert kick_steps(arr) == [15.75, 4, 4.5, 8.5, 14]
    assert all(isinstance(s, int) for s in kick_steps(arr) if s in (4, 14))
    times = _kick_times(arr)
    assert times[:5] == pytest.approx(
        [15.75 * STEP, 4 * STEP, 4.5 * STEP, 8.5 * STEP, 14 * STEP]
    )
    # ... and they are the times the kick sounds, the unrolled 0.5 included
    sounded = [
        b * 0.5 for b, _g in dict(drum_hits(replace(arr, drum_row_swing={})))["kick"]
    ]
    assert len(sounded) >= 4
    assert all(any(abs(t - x) < 1e-9 for x in times[:5]) for t in sounded)
    # the stem key follows them
    units = [{"kind": "sidechain", "params": {"depth": 0.7, "release": 0.2}}]
    assert render._chain_clock(units, arr)["kicks"] == sorted(kick_steps(arr))
    plain = _arr(cells)
    assert render._chain_clock(units, plain)["kicks"] == [0, 4, 8, 12, 14]
    assert render._chain_clock(units, arr) != render._chain_clock(units, plain)
    # a silent row is silent whatever its hits say
    assert kick_steps(replace(arr, drum_mix={"kick": {"mute": True}})) == []


def test_a_bank_slot_and_a_pool_pattern_carry_the_table_both_ways(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement = _arr(BEAT["drums"], BEAT["hits"], drum_row_swing=BEAT["swing"])
    assert s.bank_save(7, "ghosts")
    assert s.drum_bank[7]["hits"] == BEAT["hits"]
    assert s.drum_bank[7]["row_swing"] == {"hat": 0.2}
    assert s.bank_load(0)  # a factory slot has none: the loop's are cleared
    assert s.arrangement.drum_hits == {} and s.arrangement.drum_row_swing == {}
    assert "hits" not in s.drum_bank[0]
    assert s.bank_load(7)
    assert s.arrangement.drum_hits == BEAT["hits"]
    sid = s.loop_to_section("A")
    drums_t = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    pid = s.song["sections"][0]["cells"][drums_t["id"]]
    assert s.song["patterns"][pid]["hits"] == BEAT["hits"]
    assert s.song["patterns"][pid]["swing"] == {"hat": 0.2}
    s.arrangement = _arr([["kick", 0]])
    assert s.section_to_loop(sid)
    assert s.arrangement.drum_hits == BEAT["hits"]
    assert s.arrangement.drum_row_swing == {"hat": 0.2}
    s.arrangement = _arr([["kick", 0]], {"kick.0": {"r": 2}})
    assert s.apply_pool_beat(pid)
    assert s.arrangement.drum_hits == BEAT["hits"]
