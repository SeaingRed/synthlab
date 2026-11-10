"""Section rendering: a section renders ONE cycle through the
unchanged engine (`song_model.section_arrangement` -> `render.render_section`),
long cycles render in <= 10 s chunks stitched sample for sample, and the
previous section's tail folds into the next one's head. The studio caches
sections in its one RenderCache (`section:` keys) and their stems in its
StemCache, so an unchanged section costs nothing to render again."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR, n_samples
from synthlab.studio import render
from synthlab.studio import song_model as sm
from synthlab.studio.render import (
    RenderCache,
    StemCache,
    render_section,
    render_stems,
    section_audio,
    section_windows,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState
from synthlab.synth import Patch

FIXTURES = Path(__file__).parents[1] / "fixtures" / "legacy_states"
REVERB = [{"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5}}]
SHORT = Patch(name="short", release=0.05).to_json()


def _song(bpm: float = 120.0) -> dict:
    return sm.new_song(bpm, "A1", "minor")


def _notes(cells=(("A2", 0),), bars=1, spb=4) -> dict:
    return {"cells": [list(c) for c in cells], "bars": bars, "steps_per_bar": spb}


def _kick(bars=1, steps=((0),)) -> dict:
    return {"drums": [["kick", s] for s in steps], "bars": bars, "steps": 4}


def _one(song, *, notes=None, drums=None, bars=1, **synth) -> tuple[str, dict]:
    """A song with one section holding up to one notes and one drums
    pattern; returns (section id, track ids)."""
    ids = {}
    sec = sm.add_section(song, "S", bars)
    if drums is not None:
        ids["d"] = next(
            (t["id"] for t in song["tracks"] if t["kind"] == "drums"), None
        ) or sm.add_track(song, "drums", "Drums")
        sm.set_cell(song, sec, ids["d"], sm.add_pattern(song, "drums", "b", drums))
    if notes is not None:
        ids["s"] = next(
            (t["id"] for t in song["tracks"] if t["kind"] == "synth"), None
        ) or sm.add_track(song, "synth", "Lead", **synth)
        sm.set_cell(song, sec, ids["s"], sm.add_pattern(song, "notes", "n", notes))
    return sec, ids


def _sec(song, sid):
    return song["sections"][sm.section_index(song, sid)]


# --- cycle -----------------------------------------------------------------------


def test_the_cycle_is_one_pass_and_the_timeline_tiles_it():
    """A 4-bar riff over a 2-bar beat in an 8-bar section: a 4-bar cycle,
    played twice per repeat. The render is exactly one cycle long."""
    song = _song(120.0)
    sid, _ = _one(song, notes=_notes(bars=4), drums=_kick(bars=2), bars=8)
    sec = _sec(song, sid)
    sec["repeats"] = 2
    parts = sm.section_arrangement(song, sec)
    n = n_samples(4 * 2.0)  # 4 bars at 120 bpm
    assert parts.cycle_bars == 4 and not parts.flat
    assert sm.cycle_samples(4, 120.0) == n
    assert sm.section_timeline(song, sec) == [(i * n, n) for i in range(4)]
    out = render_section(parts, prev_tail=None, stem_cache=None, clip_root=None)
    assert out.shape == (n, 2)


def test_a_cycle_that_does_not_divide_the_section_is_the_whole_section():
    """A 3-bar riff in a 4-bar section: no short pass. The cycle is the
    section (flat: the riff plays once and starts again for one bar), so
    the section always ends on a cycle's end, which is what rings on."""
    song = _song(120.0)
    sid, _ = _one(song, notes=_notes(bars=3), bars=4)
    sec = _sec(song, sid)
    sec["repeats"] = 2
    n = n_samples(4 * 2.0)
    assert sm.cycle_bars(song, sec) == 4
    assert sm.section_timeline(song, sec) == [(0, n), (n, n)]
    parts = sm.section_arrangement(song, sec)
    assert parts.flat and parts.arr.cells == [["A2", 0], ["A2", 12]]


def test_a_take_makes_the_whole_section_one_cycle():
    song = _song()
    sid, _ = _one(song, notes=_notes(bars=2), bars=8)
    audio = sm.add_track(song, "audio", "Vox")
    _sec(song, sid)["audio"].append(
        {"track_id": audio, "clip": "take", "at_bar": 6, "gain": 1.0}
    )
    parts = sm.section_arrangement(song, _sec(song, sid))
    assert sm.cycle_bars(song, _sec(song, sid)) == 8
    # the 2-bar riff is tiled over the 8-bar cycle; the take stays at bar 6
    assert parts.flat and parts.arr.bars == 8
    assert [c for c in parts.arr.cells] == [["A2", 8 * i] for i in range(4)]
    assert [c["at_bar"] for c in parts.audio_clips] == [6]


def test_a_cycle_cut_short_by_the_section_is_flattened():
    """3- and 4-bar patterns in a 4-bar section: the cycle is the section
    (4), so the 3-bar beat plays once and starts again for one bar."""
    song = _song()
    sid, _ = _one(song, notes=_notes(bars=4), drums=_kick(bars=3), bars=4)
    parts = sm.section_arrangement(song, _sec(song, sid))
    assert parts.flat and parts.cycle_bars == 4
    assert (parts.arr.bars, parts.arr.drum_bars) == (4, 4)
    assert parts.arr.drums == [["kick", 0], ["kick", 12]]


def test_a_layer_on_another_grid_is_tiled_onto_the_main_lanes():
    song = _song()
    sid, ids = _one(song, notes=_notes(bars=4), bars=4)
    layer = sm.add_track(song, "synth", "Pad")
    two_bar_eighths = _notes(cells=(("C3", 2), ("C3", 3)), bars=2, spb=8)
    sm.set_cell(song, sid, layer, sm.add_pattern(song, "notes", "p", two_bar_eighths))
    parts = sm.section_arrangement(song, _sec(song, sid))
    assert parts.flat and parts.arr.bars == 4 and parts.arr.steps_per_bar == 4
    (lay,) = parts.layers
    # eighths 2 and 3 both start in quarter 1; the 2-bar layer plays twice
    assert lay["cells"] == [["C3", 1], ["C3", 9]]


# --- chunks ----------------------------------------------------------------------


def test_a_long_cycle_renders_in_chunks(monkeypatch):
    """3-bar and 4-bar patterns at 60 bpm: a 12-bar, 48 s cycle, past the
    10 s cap, so it renders as 2-bar (8 s) chunks."""
    song = _song(60.0)
    sid, _ = _one(song, notes=_notes(bars=4), drums=_kick(bars=3), bars=12)
    parts = sm.section_arrangement(song, _sec(song, sid))
    assert parts.cycle_bars == 12
    assert section_windows(parts) == [(b, b + 2) for b in range(0, 12, 2)]
    calls = []
    real = sm.SectionParts.window
    monkeypatch.setattr(
        sm.SectionParts, "window", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    out = render_section(parts, prev_tail=None, stem_cache=None, clip_root=None)
    assert len(calls) == 6
    assert out.shape == (n_samples(48.0), 2) and np.all(np.isfinite(out))
    # the last chunk sounds: its kick (bar 9) and its riff note (bar 8)
    assert np.max(np.abs(out[n_samples(32.0) :])) > 0.05


def _pad(t: np.ndarray, m: int) -> np.ndarray:
    return np.vstack([t, np.zeros((m - len(t), 2))])


def _chunked_vs_whole(parts, monkeypatch):
    """(one-piece, chunked) section audio of the same flat cycle: the cap
    at 10 s renders the 4-bar cycle whole, at 4 s in two 2-bar chunks."""
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    assert len(section_windows(parts)) == 1
    whole = section_audio(parts)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert len(section_windows(parts)) == 2
    return whole, section_audio(parts)


def _assert_same(a, b, atol=1e-9):
    assert a.base.shape == b.base.shape
    np.testing.assert_allclose(a.base, b.base, atol=atol)
    m = max(len(a.tail), len(b.tail))
    np.testing.assert_allclose(_pad(a.tail, m), _pad(b.tail, m), atol=atol)


FX_CASES = {
    "dry": [],
    "reverb": REVERB,
    "drive": [{"kind": "drive", "params": {"amount": 3.5, "mix": 1.0}}],
    "compress": [{"kind": "compress", "params": {"threshold": -30.0, "ratio": 8.0}}],
}


@pytest.mark.parametrize("fx", FX_CASES.values(), ids=FX_CASES.keys())
def test_chunks_stitch_sample_for_sample(monkeypatch, fx):
    """The chunked path gives the one-piece render back, FX included: the
    notes render dry per chunk, and each stem's chain runs once over the
    stitched cycle (a reverb/drive/compressor run per chunk stepped its
    level at every seam). A note ringing over a chunk boundary carries on,
    and a note HELD over it (C3 on steps 6-9) is not attacked again."""
    song = _song(120.0)
    cells = [("A2", s) for s in range(0, 16, 3)] + [("C3", s) for s in range(6, 10)]
    sid, _ = _one(song, notes=_notes(cells=cells, bars=4), drums=_kick(bars=2), bars=4)
    for t in song["tracks"]:
        t["fx"] = fx
    parts = sm.section_arrangement(song, _sec(song, sid)).flatten()
    whole, chunked = _chunked_vs_whole(parts, monkeypatch)
    _assert_same(chunked, whole)
    assert len(chunked.tail) <= len(chunked.base)  # at most one cycle
    if not fx:  # linear: the flat cycle is the section looping as it is
        native = sm.section_arrangement(song, _sec(song, sid))
        _assert_same(section_audio(native), whole)


def test_a_chunked_arp_plays_the_whole_patterns_arp(monkeypatch):
    """An arp arpeggiates every pitch of its pattern on every step. A chunk
    holds only some of those notes (here bar 2-3 holds only E3), so each
    chunk plays the whole cycle's pitch set, its phase counted from the
    cycle's start, and the result is the one-piece arp (and the pattern
    looping on its own)."""
    song = _song(120.0)
    notes = _notes(cells=(("A2", 0), ("C3", 5), ("E3", 12)), bars=4)
    notes["arp_mode"] = "up"
    sid, _ = _one(song, notes=notes, bars=4)
    native = sm.section_arrangement(song, _sec(song, sid))
    parts = native.flatten()
    assert parts.arp == (("A2", "C3", "E3"), 0, 16)
    whole, chunked = _chunked_vs_whole(parts, monkeypatch)
    assert np.max(np.abs(whole.base)) > 0.1
    _assert_same(chunked, whole)
    _assert_same(section_audio(native), whole)


def test_a_take_hands_on_nothing_and_its_stem_is_one_cycle(tmp_path):
    """A take is cut at the section's end: nothing of it rings into the
    next section, and its cached stem holds the cycle only (not 2x)."""
    from synthlab.studio.clips import save_clip

    save_clip(tmp_path, "take", np.full((3 * SR, 2), 0.25))  # 3 s > the 1-bar cycle
    song = _song(120.0)
    audio = sm.add_track(song, "audio", "Vox")
    sid = sm.add_section(song, "S", 1)
    _sec(song, sid)["audio"].append(
        {"track_id": audio, "clip": "take", "at_bar": 0, "gain": 1.0}
    )
    parts = sm.section_arrangement(song, _sec(song, sid))
    cache = StemCache()
    a = section_audio(parts, stem_cache=cache, clip_root=tmp_path)
    n = sm.cycle_samples(1, 120.0)
    assert np.allclose(a.base, 0.25, atol=1e-3) and not np.any(a.tail)
    assert [cache.get(k).shape for k in cache.keys()] == [(n, 2)]


def test_a_tiled_pattern_attacks_again_on_every_repeat():
    """Flattening tiles a 1-bar pattern held on every step over a 4-bar
    cycle. Its repeats must not join into one 4-bar note: the flat cycle
    sounds exactly like the pattern looping on its own."""
    song = _song(120.0)
    held = _notes(cells=[("A2", s) for s in range(4)], bars=1)
    sid, _ = _one(song, notes=held, drums=_kick(bars=4), bars=4)
    parts = sm.section_arrangement(song, _sec(song, sid))
    assert not parts.flat
    flat = parts.flatten()
    assert flat.flat and flat.breaks == (4, 8, 12)
    looped, tiled = section_audio(parts), section_audio(flat)
    np.testing.assert_allclose(tiled.base, looped.base, atol=1e-9)


# --- a layer track's own sequencing -------------------------------------------------

# a layer's pattern: its own velocities plus an arp, or plus slides
_OWN_SEQ = {
    "arp": {"velocities": {"0": 0.3, "2": 1.0, "5": 0.5}, "arp_mode": "updown"},
    "slides": {"velocities": {"0": 0.3, "3": 1.0}, "slides": [3, 6]},
    "velocities": {"velocities": {"0": 0.2, "3": 1.0, "6": 0.4}},
}
_LAYER_CELLS = (("C3", 0), ("E3", 3), ("E3", 4), ("G3", 6))


def _layer_song(seq: dict, main_bars: int) -> tuple[dict, str, str, str]:
    """A section with a plain Lead (1 or 2 bars of eighths) over a Pad
    whose 1-bar pattern has its own `seq`; a second section where that
    Pad pattern is the main lane, alone."""
    song = _song(120.0)
    lead = sm.add_track(song, "synth", "Lead", patch=SHORT)
    pad = sm.add_track(
        song,
        "synth",
        "Pad",
        patch=Patch(name="pad", osc1="saw", release=0.05).to_json(),
    )
    both = sm.add_section(song, "Both", main_bars)
    solo = sm.add_section(song, "Solo", 1)
    riff = _notes((("A2", 1), ("A2", 2)), bars=main_bars, spb=8)
    own = sm.add_pattern(song, "notes", "own", _notes(_LAYER_CELLS, spb=8) | seq)
    sm.set_cell(song, both, lead, sm.add_pattern(song, "notes", "riff", riff))
    sm.set_cell(song, both, pad, own)
    sm.set_cell(song, solo, pad, own)
    return song, both, solo, pad


def _looped(song, sid) -> np.ndarray:
    parts = sm.section_arrangement(song, _sec(song, sid))
    return render_section(parts, prev_tail=None, stem_cache=None, clip_root=None)


@pytest.mark.parametrize("main_bars", [1, 2], ids=["same_grid", "tiled"])
@pytest.mark.parametrize("seq", _OWN_SEQ.values(), ids=_OWN_SEQ.keys())
def test_a_layer_track_plays_its_own_patterns_sequencing(seq, main_bars):
    """A layer track plays its pattern's velocities, slides and arp exactly
    as that pattern plays as a section's main lane (`lane_render` ->
    `arrange._lane_timeline`), in a section on the main lane's own grid
    and in a tiled (flat) one, where each repeat restarts the arp."""
    song, both, solo, pad = _layer_song(seq, main_bars)
    parts = sm.section_arrangement(song, _sec(song, both))
    assert parts.flat is (main_bars == 2)
    (lay,) = parts.layers
    assert lay["velocities"]
    assert ("arp_mode" in lay) is ("arp_mode" in seq)
    assert ("slides" in lay) is ("slides" in seq)
    if "arp_mode" in seq and parts.flat:
        assert lay["arp"] == [["C3", "E3", "G3"], 0, 8]
    mixed = _looped(song, both)
    song["tracks"][sm.track_index(song, pad)]["mute"] = True
    layer = mixed - _looped(song, both)
    song["tracks"][sm.track_index(song, pad)]["mute"] = False
    alone = np.tile(_looped(song, solo), (main_bars, 1))
    assert np.max(np.abs(alone)) > 0.05
    np.testing.assert_allclose(layer, alone, atol=1e-9)


def test_a_chunked_layer_arp_plays_the_whole_patterns_arp(monkeypatch):
    """A layer's arp and velocities crop into chunks like the main lane's
    (`_crop_layer` moves the arp's phase), so the chunked flat cycle is
    the one-piece render."""
    song, both, _solo, _pad = _layer_song(_OWN_SEQ["arp"], 4)
    parts = sm.section_arrangement(song, _sec(song, both)).flatten()
    whole, chunked = _chunked_vs_whole(parts, monkeypatch)
    _assert_same(chunked, whole)


# --- tails -----------------------------------------------------------------------


def _pair(fx_a) -> tuple[dict, str, str]:
    """Section A (one short note on beat 1, on a track with `fx_a`) then
    section B (one note mid-bar, on another track)."""
    song = _song(120.0)
    a = sm.add_track(song, "synth", "A", patch=SHORT, fx=fx_a)
    b = sm.add_track(song, "synth", "B", patch=SHORT)
    sa = sm.add_section(song, "A", 1)
    sb = sm.add_section(song, "B", 1)
    sm.set_cell(song, sa, a, sm.add_pattern(song, "notes", "a", _notes((("A2", 1),))))
    sm.set_cell(song, sb, b, sm.add_pattern(song, "notes", "b", _notes((("E2", 2),))))
    return song, sa, sb


def test_a_reverb_tail_folds_into_the_next_sections_head():
    song, sa, sb = _pair(REVERB)
    a = section_audio(sm.section_arrangement(song, _sec(song, sa)))
    parts_b = sm.section_arrangement(song, _sec(song, sb))
    cold = render_section(
        parts_b, prev_tail=np.zeros((0, 2)), stem_cache=None, clip_root=None
    )
    after_a = render_section(parts_b, prev_tail=a.tail, stem_cache=None, clip_root=None)
    head = slice(0, SR // 4)  # B is silent until beat 2
    assert np.max(np.abs(cold[head])) < 1e-9
    assert np.sqrt(np.mean(after_a[head] ** 2)) > 1e-3
    # the difference is A's tail, laid over B's head, and nothing else
    folded = np.zeros_like(cold)
    folded[: len(a.tail)] = a.tail[: len(cold)]
    np.testing.assert_allclose(after_a - cold, folded, atol=1e-12)


def test_a_dry_section_leaves_the_next_head_alone():
    song, sa, sb = _pair([])
    a = section_audio(sm.section_arrangement(song, _sec(song, sa)))
    assert not np.any(a.tail)  # the short note has died by the bar line
    parts_b = sm.section_arrangement(song, _sec(song, sb))
    cold = render_section(
        parts_b, prev_tail=np.zeros((0, 2)), stem_cache=None, clip_root=None
    )
    after_a = render_section(parts_b, prev_tail=a.tail, stem_cache=None, clip_root=None)
    np.testing.assert_array_equal(after_a, cold)


def test_a_looped_section_folds_onto_itself_as_a_loop_does():
    """prev_tail=None: the section folds its own tail onto its head, the
    way `_wrap_tile` folds a loop — equal to the legacy stems' sum."""
    song, sa, _ = _pair(REVERB)
    parts = sm.section_arrangement(song, _sec(song, sa))
    looped = render_section(parts, prev_tail=None, stem_cache=None, clip_root=None)
    legacy = sum(stem for _n, stem in render_stems(parts.inputs()))
    np.testing.assert_allclose(looped, legacy, atol=1e-12)


def test_a_migrated_scene_loops_as_the_scene_did(tmp_path):
    legacy = json.loads((FIXTURES / "with_scenes.json").read_text(encoding="utf-8"))
    (tmp_path / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
    s = StudioState(tmp_path)
    for sec in s.song["sections"]:
        parts = sm.section_arrangement(s.song, sec)
        looped = render_section(
            parts, prev_tail=None, stem_cache=None, clip_root=tmp_path
        )
        stems = render_stems(parts.inputs(clip_root=tmp_path))
        np.testing.assert_allclose(looped, sum(x for _n, x in stems), atol=1e-12)


# --- the studio's cache ------------------------------------------------------------


def _three(song: dict) -> tuple[list[str], dict]:
    """Sections X, Y, X': X and X' share pattern P, Y plays Q."""
    t = sm.add_track(song, "synth", "Lead", patch=SHORT)
    p = sm.add_pattern(song, "notes", "P", _notes((("A2", 0),)))
    q = sm.add_pattern(song, "notes", "Q", _notes((("C3", 1),)))
    sids = [sm.add_section(song, n, 1) for n in ("X", "Y", "X2")]
    for sid, pid in zip(sids, (p, q, p), strict=True):
        sm.set_cell(song, sid, t, pid)
    return sids, {"p": p, "q": q}


@pytest.fixture
def svc(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        yield client.app.state.svc


def _spy_builds(monkeypatch) -> list:
    calls: list = []
    real = render.build_song
    monkeypatch.setattr(
        render, "build_song", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    return calls


def test_an_unchanged_section_is_not_rendered_again(svc, monkeypatch):
    song = _song()
    _three(song)
    first = svc.section_result(song, 1)
    assert first.key.startswith("section:")
    assert svc.cache.get(first.key) is first  # served by /preview/{key}.wav
    builds = _spy_builds(monkeypatch)
    assert svc.section_result(song, 1) is first
    svc.cache = RenderCache()
    again = svc.section_result(song, 1)  # the finished WAV is gone: stems hit
    assert builds == [] and again.wav == first.wav


def test_editing_a_pattern_rerenders_only_the_sections_that_use_it(svc, monkeypatch):
    song = _song()
    sids, ids = _three(song)
    before = [svc.section_result(song, i).key for i in range(3)]
    builds = _spy_builds(monkeypatch)
    song["patterns"][ids["q"]]["cells"] = [["D3", 2]]  # Y's pattern
    after = [svc.section_result(song, i).key for i in range(3)]
    assert after[0] == before[0]
    assert after[1] != before[1]  # Y uses Q
    assert after[2] != before[2]  # X2's head folds Y's tail in...
    assert len(builds) == 1  # ...but only Y's one stem rendered again


def test_the_next_sections_key_follows_the_previous_section(svc):
    song = _song()
    sids, ids = _three(song)
    loop = svc.section_result(song, 2, loop=True).key
    head = svc.section_result(song, 2).key
    assert loop != head  # a loop folds onto itself, a song pass onto Y
    song["patterns"][ids["q"]]["cells"] = [["D3", 2]]
    assert svc.section_result(song, 2, loop=True).key == loop
    assert svc.section_result(song, 2).key != head


def test_section_result_refuses_a_bad_index(svc):
    song = _song()
    _three(song)
    for bad in (-1, 3, 99):
        with pytest.raises(render.RenderError):
            svc.section_result(song, bad)


def test_the_main_lanes_track_gain_applies_like_a_layers():
    """Ruling: a track's gain applies whichever lane it plays, so moving a
    track up to main never changes whether its gain is heard."""

    def main_stem(gain):
        song = _song(120.0)
        sid, _ = _one(song, notes=_notes(), bars=1, patch=SHORT, gain=gain)
        parts = sm.section_arrangement(song, _sec(song, sid))
        assert parts.main_gain == gain
        return dict(render_stems(parts.inputs()))["main"]

    np.testing.assert_allclose(main_stem(0.5), 0.5 * main_stem(1.0), atol=1e-12)


def test_the_stem_cache_stays_inside_its_byte_budget(svc, monkeypatch):
    """Long flat cycles make big stems (a 48 s cycle is ~34-68 MB a stem); the
    StemCache is bounded in bytes, not only in entries."""
    from synthlab.studio import limits

    # 8 x 48 s is over the song cap, which section_result enforces;
    # this test is about the stem cache, so it lifts the cap
    monkeypatch.setattr(limits, "MAX_SONG_SECONDS", 10_000.0)
    # the budget follows the machine's RAM (`render._stem_cache_bytes`);
    # the old flat one, which these eight sections overflow
    monkeypatch.setattr(svc.stems, "max_bytes", 256 << 20)
    song = _song(60.0)
    lead = sm.add_track(song, "synth", "Lead", patch=SHORT)
    pad = sm.add_track(song, "synth", "Pad")
    for i in range(8):  # 12-bar cycles at 60 bpm, each its own riffs
        sid = sm.add_section(song, f"S{i}", 12)
        riff = _notes(cells=(("A2", i),), bars=4)
        sm.set_cell(song, sid, lead, sm.add_pattern(song, "notes", f"r{i}", riff))
        other = _notes(cells=(("C3", i),), bars=3)
        sm.set_cell(song, sid, pad, sm.add_pattern(song, "notes", f"q{i}", other))
        svc.section_result(song, i, loop=True)
    stats = svc.stems.stats()
    assert stats["evictions"] > 0
    assert stats["bytes"] <= 256 << 20
    assert 256 << 20 <= render.STEM_CACHE_BYTES <= 4 << 30


@pytest.mark.parametrize("gain", [0.0, -1.0, float("nan"), float("inf"), True, "1"])
def test_section_result_refuses_a_bad_gain(svc, gain):
    song = _song()
    _three(song)
    with pytest.raises(render.RenderError):
        svc.section_result(song, 0, gain=gain)


def test_a_round_trip_through_the_loop_keeps_the_main_tracks_gain(tmp_path):
    """section -> loop -> section: the main lane's track (gain 0.5) is
    found again, not copied at 1.0, so the new section is the same sound:
    same tracks, same key, same audio."""
    from synthlab.studio import schema

    song = _song(120.0)
    sid, ids = _one(
        song, notes=_notes(bars=2), drums=_kick(bars=2), bars=2, patch=SHORT, gain=0.5
    )
    s = StudioState(tmp_path)
    s.song = schema.conform(song, schema.nodes().SONG)
    tracks = [t["id"] for t in s.song["tracks"]]
    before = sm.section_arrangement(s.song, s.song["sections"][0])
    assert s.section_to_loop(sid)
    new = s.loop_to_section("again")
    assert [t["id"] for t in s.song["tracks"]] == tracks  # no copy at 1.0
    lead = next(t for t in s.song["tracks"] if t["id"] == ids["s"])
    assert lead["gain"] == 0.5
    sec = s.song["sections"][sm.section_index(s.song, new)]
    assert sec["cells"] == s.song["sections"][0]["cells"]
    after = sm.section_arrangement(s.song, sec)
    assert render.section_key(after, tmp_path) == render.section_key(before, tmp_path)
    np.testing.assert_array_equal(
        section_audio(after, clip_root=tmp_path).base,
        section_audio(before, clip_root=tmp_path).base,
    )


def test_a_cycle_stem_is_stored_as_long_as_it_rings():
    """A flat cycle's stem keeps its real ring (last note + 4 beats + 1 s),
    not a full cycle of zero padding; a long ring stops at one cycle."""

    def stored(cells, fx):
        song = _song(120.0)
        sid, _ = _one(song, notes=_notes(cells=cells, bars=4), bars=4, patch=SHORT)
        song["tracks"][0]["fx"] = fx
        parts = sm.section_arrangement(song, _sec(song, sid)).flatten()
        cache = StemCache()
        a = section_audio(parts, stem_cache=cache)
        (stem,) = [cache.get(k) for k in cache.keys()]
        return len(stem), len(a.base), len(a.tail)

    n = n_samples(8.0)
    short, base, tail = stored((("A2", 0),), [])
    assert base == n and short == n and tail == 0  # rang out inside the cycle
    ring, _base, tail = stored((("A2", 15),), [])  # the last beat rings on
    assert n < ring < 2 * n and tail == ring - n


# --- review fixes: chunk seams, long cycles, hidden notes -------------------------


def test_a_slide_over_a_chunk_seam_glides_as_in_one_piece(monkeypatch):
    """Each chunk of a cycle builds its own song, so a slide on a chunk's
    first note had nothing to glide from: no from-pitch, no forced glide,
    and the chunked cycle left the one-piece render at the seam. The chunk
    now carries the pitch before it (`SectionParts.lead`), in its key too."""
    from dataclasses import replace as dc_replace

    song = _song(120.0)
    notes = _notes(cells=(("A2", 2), ("A2", 6), ("E3", 8), ("C3", 12)), bars=4)
    notes["slides"] = [8, 12]
    sid, _ = _one(song, notes=notes, bars=4)
    parts = sm.section_arrangement(song, _sec(song, sid)).flatten()
    first, second = parts.window(0, 2), parts.window(2, 4)
    assert first.lead is None and second.lead == "A2"
    assert second.inputs().key() != dc_replace(second, lead=None).inputs().key()
    whole, chunked = _chunked_vs_whole(parts, monkeypatch)
    assert np.max(np.abs(whole.base)) > 0.1
    _assert_same(chunked, whole)


def test_a_chunk_without_a_slide_on_its_first_note_keeps_its_key():
    song = _song(120.0)
    notes = _notes(cells=(("A2", 2), ("E3", 8), ("C3", 12)), bars=4)
    notes["slides"] = [12]  # not the chunk's first note
    sid, _ = _one(song, notes=notes, bars=4)
    parts = sm.section_arrangement(song, _sec(song, sid)).flatten()
    assert parts.window(2, 4).lead is None
    assert "leads" not in render._cycle_fixes(parts)


def test_a_layers_slide_over_a_chunk_seam_glides_too(monkeypatch):
    song, both, _solo, _pad = _layer_song({"slides": [0, 3]}, 4)
    parts = sm.section_arrangement(song, _sec(song, both)).flatten()
    assert parts.window(2, 4).layers[0]["lead"] == "G3"
    whole, chunked = _chunked_vs_whole(parts, monkeypatch)
    _assert_same(chunked, whole)


def test_a_lanes_pump_runs_to_the_end_of_a_long_cycle():
    """A flat cycle's stem FX run once over the whole stitched cycle, and
    the chain was built without its length: a pump has 256 triggers by
    default, so a 1/16 pump at 200 bpm stopped ducking 19.2 s into a 24 s
    cycle."""
    pump = [{"kind": "pump", "params": {"depth": 1.0, "release": 0.07, "beats": 0.25}}]
    held = [("A2", s) for s in range(12)]

    def cycle(fx):
        song = _song(200.0)
        pad = Patch(name="pad", attack=0.01, release=0.05).to_json()
        sid, _ = _one(song, notes=_notes(cells=held, bars=3), bars=20, patch=pad)
        song["tracks"][0]["fx"] = fx
        parts = sm.section_arrangement(song, _sec(song, sid))
        assert parts.flat and parts.cycle_bars == 20
        return parts, section_audio(parts).base

    parts, wet = cycle(pump)
    _dry_parts, dry = cycle([])

    def level(x, t0, t1):
        return float(np.sqrt(np.mean(x[n_samples(t0) : n_samples(t1)] ** 2)))

    early = level(wet, 2.0, 6.0) / level(dry, 2.0, 6.0)
    late = level(wet, 19.6, 23.6) / level(dry, 19.6, 23.6)
    assert early < 0.8  # it ducks ...
    assert abs(late - early) < 0.05  # ... all the way to the end
    assert render._cycle_fixes(parts) == {"pump_span_v": 2}


def test_a_sections_arp_leaves_out_notes_hidden_past_the_grid(monkeypatch):
    """A note kept past a shrunk grid is hidden and does not play; a loop
    arpeggiates only the rows that do (`arrange._playing_steps`). A flat
    section took its arp's pitches from every cell, the hidden C3 included."""
    song = _song(120.0)
    notes = _notes(cells=(("A1", 0), ("E2", 4), ("C3", 20)), spb=16)
    notes["arp_mode"] = "up"
    sid, _ = _one(song, notes=notes, bars=2)
    native = sm.section_arrangement(song, _sec(song, sid))
    assert ["C3", 20] in native.arr.cells and not native.flat  # kept, hidden
    flat = native.flatten()
    assert flat.arp == (("A1", "E2"), 0, 16)
    _assert_same(section_audio(flat), section_audio(native))


def test_a_layers_arp_leaves_out_hidden_notes_too():
    lay = {"cells": [["A1", 0], ["E2", 4], ["C3", 20]], "arp_mode": "up"}
    assert sm._tile_layer(lay, 16, 32)["arp"] == [["A1", "E2"], 0, 16]
