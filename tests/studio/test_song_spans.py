"""Block spans: `section["spans"] = {track: [at, len]}` — a cell's pattern plays bars
[at, at + len) of each pass of its section, tiled from `at`, and is silent
outside. Absent means the whole section, which is every cell of every song
that never trimmed one.

Three things are held here:

- the schema and the model: hostile values are clamped or dropped, a full
  span and an empty map are no key at all, and a song without spans keeps
  the bytes it had;
- **exactness**: a trimmed block renders what the same notes WRITTEN BY
  HAND into a section-long pattern render — `by_hand` writes them out with
  a loop of its own, never the model's tiling — to the tolerance the
  chunked-vs-one-piece tests hold (1e-9), through every path a section
  takes: one piece, chunked, repeats and tails, an automation lane, a
  whole stem under it, solo-cell, the stems and the MIDI file;
- the route (`POST /song/cell/span`), with spill.
"""

from __future__ import annotations

import copy
import io
import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import limits, midi_bridge, render, schema, song_play
from synthlab.studio import song_model as sm
from synthlab.studio.render import section_audio, section_key, section_windows
from synthlab.studio.server import create_app
from synthlab.synth import Patch
from tests.studio.test_song_routes import HX, song_dict, use_song

SHORT = Patch(name="short", release=0.05).to_json()
RING = Patch(name="ring", osc1="saw", release=0.7).to_json()  # rings past a bar
REVERB = [{"kind": "reverb", "params": {"decay": 1.2, "mix": 0.4}}]
BAR = sm.bar_sample(1, 120.0)  # 88200: a bar is a whole number of samples
ATOL = 1e-9  # test_song_render.py's: chunked against one piece


def clean(song: dict) -> dict:
    return schema.conform(song, schema.nodes().SONG)


def sec_of(song: dict, sid: str) -> dict:
    return song["sections"][sm.section_index(song, sid)]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def song_of(client) -> dict:
    return client.app.state.studio.song


# === the schema =====================================================================


def test_a_song_without_spans_has_no_such_key_and_keeps_its_bytes():
    """Absent means absent: no section gains a key, so every saved song,
    render key and fixture byte holds."""
    song = clean(song_dict())
    assert all("spans" not in sec for sec in song["sections"])
    assert "spans" not in json.dumps(song)
    assert "spans" not in schema.nodes().SECTION.default()
    assert "spans" not in json.dumps(schema.nodes().SONG.default())
    assert clean(copy.deepcopy(song)) == song
    # an empty map is not written either
    raw = song_dict()
    raw["sections"][0]["spans"] = {}
    assert clean(raw) == song


def test_a_span_is_kept_inside_its_section():
    raw = song_dict()
    raw["sections"][1]["bars"] = 4  # Verse: td, t1 and t2 (a 2-bar pattern)
    raw["sections"][1]["spans"] = {"td": [1, 2], "t1": [3, 9], "t2": [9, 1]}
    spans = clean(raw)["sections"][1]["spans"]
    assert spans == {"td": [1, 2], "t1": [3, 1], "t2": [3, 1]}  # len, then at, clamped


def test_a_span_that_covers_the_section_is_dropped():
    raw = song_dict()
    raw["sections"][0]["spans"] = {"td": [0, 2], "t1": [0, 64]}  # Intro is 2 bars
    assert "spans" not in clean(raw)["sections"][0]


@pytest.mark.parametrize(
    "spans",
    [
        {"td": "all of it"},
        {"td": [1]},
        {"td": [1, 1, 1]},
        {"td": [True, 1]},
        {"td": ["1", "1"]},
        {"td": [float("nan"), 1]},
        {"td": [1, float("inf")]},
        {"td": {"at": 1, "len": 1}},
        {"td": None},
        {"ta": [1, 1]},  # an audio track holds takes, not a block
        {"t2": [1, 1]},  # an empty cell (Intro has nothing on t2)
        {"nope": [1, 1]},  # no such track
        {"<script>": [1, 1]},
        [["td", [1, 1]]],
        "td",
        7,
        None,
    ],
)
def test_hostile_spans_are_dropped_never_an_error(spans):
    raw = song_dict()
    raw["sections"][0]["spans"] = spans
    assert "spans" not in clean(raw)["sections"][0]


def test_hostile_numbers_are_clamped():
    raw = song_dict()
    raw["sections"][0]["bars"] = 4
    raw["sections"][0]["spans"] = {"td": [-5, 2], "t1": [1.9, 0]}
    assert clean(raw)["sections"][0]["spans"] == {"td": [0, 2], "t1": [1, 1]}
    raw["sections"][0]["spans"] = {"td": [10**400, 1]}  # a 400-digit int
    assert "spans" not in clean(raw)["sections"][0]


def test_a_thousand_spans_keep_the_songs_tracks_only():
    raw = song_dict()
    raw["sections"][0]["bars"] = 4
    raw["sections"][0]["spans"] = {f"x{i}": [1, 1] for i in range(1000)} | {
        "td": [1, 1]
    }
    spans = clean(raw)["sections"][0].get("spans", {})
    assert set(spans) <= {"td"} and len(spans) <= limits.MAX_TRACKS


def test_a_section_grown_by_its_pattern_holds_its_spans():
    """`song_post` grows a section to its longest pattern first, then holds
    the spans to the bars it ends with."""
    raw = song_dict()
    raw["sections"][1]["bars"] = 1  # the Verse plays Hook, 2 bars: it grows
    raw["sections"][1]["spans"] = {"td": [1, 1]}
    sec = clean(raw)["sections"][1]
    assert sec["bars"] == 2 and sec["spans"] == {"td": [1, 1]}


# === the model ======================================================================


def model_song() -> dict:
    song = song_dict()
    song["sections"][0]["bars"] = 4
    song["sections"][1]["bars"] = 4
    return clean(song)


def test_set_span_clamps_and_a_full_span_clears():
    song = model_song()
    assert sm.cell_span(song["sections"][0], "td") == (0, 4)
    assert sm.set_span(song, "s1", "td", 1, 2) == (1, 2)
    assert song["sections"][0]["spans"] == {"td": [1, 2]}
    assert sm.cell_span(song["sections"][0], "td") == (1, 2)
    assert sm.set_span(song, "s1", "td", 3, 9) == (3, 1)
    assert sm.set_span(song, "s1", "td", -2, 0) == (0, 1)
    assert sm.set_span(song, "s1", "td", 0, 4) == (0, 4)  # the whole section
    assert "spans" not in song["sections"][0]
    sm.set_span(song, "s1", "td", 2, 1)
    sm.clear_span(song, "s1", "td")
    assert "spans" not in song["sections"][0]
    assert clean(copy.deepcopy(song)) == song


@pytest.mark.parametrize(
    "sid, tid, said",
    [
        ("s1", "t2", "empty"),  # no pattern there
        ("s1", "ta", "empty"),  # an audio track
        ("zz", "td", "No such section"),
        ("s1", "zz", "No such track"),
    ],
)
def test_set_span_refuses_what_holds_no_block(sid, tid, said):
    song = model_song()
    before = copy.deepcopy(song)
    with pytest.raises(sm.SongError, match=said):
        sm.set_span(song, sid, tid, 1, 1)
    assert song == before


def test_clearing_a_cell_or_removing_its_track_drops_the_span():
    song = model_song()
    sm.set_span(song, "s1", "td", 1, 2)
    sm.set_span(song, "s1", "t1", 2, 1)
    sm.set_cell(song, "s1", "td", None)
    assert song["sections"][0]["spans"] == {"t1": [2, 1]}
    for sec in song["sections"]:
        sm.set_cell(song, sec["id"], "t1", None)
    assert "spans" not in song["sections"][0]
    sm.set_cell(song, "s1", "t1", "p1")
    sm.set_span(song, "s1", "t1", 2, 1)
    sm.set_cell(song, "s1", "t1", None)
    assert sm.remove_track(song, "t1") == []
    assert all("spans" not in sec for sec in song["sections"])


def test_pointing_a_trimmed_cell_at_another_pattern_keeps_its_place():
    song = model_song()
    sm.set_span(song, "s1", "t1", 1, 2)
    sm.set_cell(song, "s1", "t1", "p3")  # new content, same place
    assert song["sections"][0]["spans"] == {"t1": [1, 2]}
    for change in (sm.make_unique, lambda *a: sm.variation(*a, seed=3)):
        change(song, "s1", "t1")
        assert song["sections"][0]["spans"] == {"t1": [1, 2]}


def test_duplicating_a_section_copies_its_spans():
    song = model_song()
    sm.set_span(song, "s1", "td", 1, 2)
    new = sm.duplicate_section(song, "s1")
    dup = sec_of(song, new)
    assert dup["spans"] == {"td": [1, 2]}
    dup["spans"]["td"][0] = 0
    assert song["sections"][0]["spans"] == {"td": [1, 2]}  # a copy, not shared


def test_move_cell_carries_the_span_clamped_to_where_it_lands():
    song = model_song()
    song["sections"][1]["bars"] = 2
    song = clean(song)
    sm.set_span(song, "s1", "t1", 3, 1)  # bar 4 of the 4-bar Intro
    sm.set_cell(song, "s2", "t1", None)
    assert sm.move_cell(song, "s1", "t1", "s2", "t1")
    assert "spans" not in song["sections"][0]
    assert song["sections"][1]["spans"] == {"t1": [1, 1]}  # the Verse is 2 bars
    # to another track too: a span is where the block sits
    sm.set_cell(song, "s1", "t2", None)
    assert sm.move_cell(song, "s2", "t1", "s1", "t2")
    assert song["sections"][0]["spans"] == {"t2": [1, 1]}
    assert "spans" not in song["sections"][1]
    # a block without a span fills the cell it lands on, whatever was there
    sm.set_cell(song, "s2", "t1", "p3")
    sm.set_span(song, "s2", "t1", 1, 1)
    sm.set_span(song, "s1", "t2", 0, 4)
    assert sm.move_cell(song, "s1", "t2", "s2", "t1")
    assert "spans" not in song["sections"][1]
    assert clean(copy.deepcopy(song)) == song


def spill_song() -> dict:
    """Five 2-bar sections over one synth track: A plays Riff; B is empty;
    C plays Riff from its second bar; D plays Spare; E is empty."""
    song = song_dict()
    song["tracks"] = [t for t in song["tracks"] if t["id"] == "t1"]
    song["sections"] = [
        {"id": s, "name": s.upper(), "bars": 2, "cells": cells}
        for s, cells in (
            ("a", {"t1": "p1"}),
            ("b", {}),
            ("c", {"t1": "p1"}),
            ("d", {"t1": "p3"}),
            ("e", {}),
        )
    ]
    song["sections"][2]["spans"] = {"t1": [1, 1]}
    return clean(song)


def _blocks(song: dict) -> list:
    return [
        (
            sec["cells"].get("t1"),
            sm.cell_span(sec, "t1") if sec["cells"].get("t1") else None,
        )
        for sec in song["sections"]
    ]


def test_spill_fills_empty_cells_and_stops_at_the_last_bar_asked():
    song = spill_song()
    sm.set_span(song, "a", "t1", 0, 1)
    assert sm.spill_span(song, "a", "t1", "b", 1) == ["B"]
    assert _blocks(song)[:2] == [("p1", (0, 2)), ("p1", (0, 1))]
    assert song["sections"][0].get("spans") is None  # A runs to its end again
    assert song["sections"][1]["spans"] == {"t1": [0, 1]}


def test_spill_extends_the_same_pattern_and_stops_at_another():
    song = spill_song()
    assert sm.spill_span(song, "a", "t1", "e", 2) == ["B", "C"]
    assert _blocks(song) == [
        ("p1", (0, 2)),
        ("p1", (0, 2)),  # the empty cell took the pattern, whole
        ("p1", (0, 2)),  # the same pattern, from its bar 2: extended to cover
        ("p3", (0, 2)),  # another pattern: the spill stopped here
        (None, None),  # ... and never reached E
    ]
    assert all("spans" not in sec for sec in song["sections"])


def test_spill_never_shortens_a_block_already_there():
    song = spill_song()
    sm.set_span(song, "c", "t1", 0, 2)
    sm.clear_span(song, "c", "t1")
    assert sm.spill_span(song, "a", "t1", "c", 1) == ["B", "C"]
    assert _blocks(song)[2] == ("p1", (0, 2))  # asked for 1 bar; it had 2


def test_spill_stops_at_a_section_shorter_than_the_pattern():
    song = spill_song()
    song["patterns"]["p1"]["bars"] = 2
    song["sections"][1]["bars"] = 1  # B would have to grow
    before = copy.deepcopy(song["sections"][1:])
    assert sm.spill_span(song, "a", "t1", "c", 2) == []
    assert song["sections"][1:] == before  # nothing past A was touched
    assert song["sections"][1]["bars"] == 1


def test_spill_keeps_the_blocks_own_start():
    song = spill_song()
    sm.set_span(song, "a", "t1", 1, 1)
    sm.spill_span(song, "a", "t1", "b", 2)
    assert _blocks(song)[:2] == [("p1", (1, 1)), ("p1", (0, 2))]


# === exactness: a trimmed block is its notes written by hand =========================
#
# `by_hand` is the reference: the pattern's notes copied out from the block's
# first bar, again every pattern length, up to the block's last bar — with a
# plain while loop, not the model's `_tile`. Where a copy starts on a pitch the
# copy before it was still holding, the notes are separated by a cut (what
# Shift-click writes: "start a new note here"), because a repeat of a pattern
# attacks again.


def by_hand(p: dict, at: int, n: int, bars: int) -> dict:
    s = p["steps_per_bar"]
    period, lo, hi = p["bars"] * s, at * s, (at + n) * s
    cells, vel, slides, cuts = [], {}, [], []
    start = lo
    while start < hi:
        for pitch, st in p["cells"]:
            if st < period and start + st < hi:
                cells.append([pitch, start + st])
        for k, v in p.get("velocities", {}).items():
            if int(k) < period and start + int(k) < hi:
                vel[str(start + int(k))] = v
        slides += [
            start + x for x in p.get("slides", []) if x < period and start + x < hi
        ]
        cuts += [
            [pitch, start + st]
            for pitch, st in p.get("cuts", [])
            if st < period and start + st < hi
        ]
        if start > lo:
            cuts += [[pitch, start] for pitch, st in p["cells"] if st == 0]
        start += period
    out = {"bars": bars, "steps_per_bar": s, "cells": cells, "velocities": vel}
    return out | {"slides": slides, "cuts": cuts}


def drums_by_hand(p: dict, at: int, n: int, bars: int) -> dict:
    s = p["steps"]
    period, lo, hi = p["bars"] * s, at * s, (at + n) * s
    hits, vel = [], {}
    start = lo
    while start < hi:
        hits += [
            [v, start + st] for v, st in p["drums"] if st < period and start + st < hi
        ]
        for k, v in p.get("drum_vel", {}).items():
            if int(k) < period and start + int(k) < hi:
                vel[str(start + int(k))] = v
        start += period
    return {"bars": bars, "steps": s, "drums": hits, "drum_vel": vel}


# a 1-bar riff: A2 held two steps, E3, and C3 held over the bar's last two
# steps AND on its first — so a repeat must attack C3 again, not hold it on
RIFF = {
    "bars": 1,
    "steps_per_bar": 16,
    "cells": [["A2", 0], ["A2", 1], ["C3", 0], ["E3", 5], ["C3", 14], ["C3", 15]],
    "velocities": {"0": 0.6, "5": 1.0, "14": 0.8},
}
# the same with a slide into E3 and a cut inside the held A2
SLID = RIFF | {"slides": [5], "cuts": [["A2", 1]]}
# a 2-bar line whose second bar holds a note to its very end
LINE = {
    "bars": 2,
    "steps_per_bar": 16,
    "cells": [["G2", 2], ["D3", 9], ["D3", 10]] + [["F2", s] for s in range(26, 32)],
    "velocities": {"2": 0.9, "9": 0.5, "26": 0.7},
}
BEAT = {
    "bars": 1,
    "steps": 16,
    "drums": [["kick", 0], ["snare", 8], ["hat", 4], ["hat", 12], ["kick", 14]],
    "drum_vel": {"0": 1.0, "8": 0.8, "14": 0.5},
}


def build(spans: dict, *, hand: bool, bars: int = 4, lead=RIFF, patch=SHORT, **more):
    """A song with one `bars`-long section "s" over a Lead (`lead`), a Pad
    (LINE) and Drums (BEAT), each block at `spans[track]` = (at, len) (the
    whole section when not named). `hand`: instead of spans, each cell
    plays the same notes written out by hand in a section-long pattern."""
    song = sm.new_song(120.0, "A1", "minor")
    song["tracks"] = [
        {"id": "lead", "kind": "synth", "name": "Lead", "patch": patch},
        {"id": "pad", "kind": "synth", "name": "Pad", "patch": RING},
        {"id": "drums", "kind": "drums", "name": "Drums"},
        {"id": "aud", "kind": "audio", "name": "Aud"},
    ]
    own = {"lead": lead, "pad": LINE, "drums": BEAT}
    cells, pats = {}, {}
    for tid, p in own.items():
        if tid in more.get("without", ()):
            continue
        at, n = spans.get(tid, (0, bars))
        kind = "drums" if tid == "drums" else "notes"
        if hand:
            p = (drums_by_hand if kind == "drums" else by_hand)(p, at, n, bars)
        pats[tid] = {"id": tid, "kind": kind, "name": tid, **copy.deepcopy(p)}
        cells[tid] = tid
    song["patterns"] = pats
    sec = {"id": "s", "name": "S", "bars": bars, "cells": cells}
    if not hand:
        sec["spans"] = {t: list(v) for t, v in spans.items() if t in cells}
    song["sections"] = [sec | more.get("section", {})]
    return clean(song)


def parts_of(song: dict, sid: str = "s"):
    return sm.section_arrangement(song, sec_of(song, sid))


def same(a, b, atol=ATOL):
    """Two section renders: the cycle and what rings past it."""
    assert a.base.shape == b.base.shape
    np.testing.assert_allclose(a.base, b.base, atol=atol)
    m = max(len(a.tail), len(b.tail))

    def pad(t):
        return np.vstack([t, np.zeros((m - len(t), 2))])

    np.testing.assert_allclose(pad(a.tail), pad(b.tail), atol=atol)


def looped(one, passes: int) -> np.ndarray:
    """`passes` cycles of a section render end to end, each one's tail
    ringing into what follows (and past the last, for a bar)."""
    n = len(one.base)
    out = np.zeros(((passes + 1) * n, 2))
    for i in range(passes):
        out[i * n : (i + 1) * n] += one.base
        ring = one.tail[: (passes - i) * n]
        out[(i + 1) * n : (i + 1) * n + len(ring)] += ring
    return out


ONLY_LEAD = {"without": ("pad", "drums")}

SPANS = {
    "main trimmed": {"lead": (1, 2)},
    "main late, layer early": {"lead": (2, 2), "pad": (0, 3)},
    "layer cut mid-pattern": {"pad": (1, 3)},  # LINE is 2 bars: one and a half
    "drums stop a bar early": {"drums": (0, 3)},
    "all three": {"lead": (1, 3), "pad": (2, 2), "drums": (1, 2)},
    "one bar each": {"lead": (3, 1), "pad": (0, 1), "drums": (2, 1)},
}


@pytest.mark.parametrize("spans", SPANS.values(), ids=SPANS.keys())
@pytest.mark.parametrize("lead", [RIFF, SLID], ids=["riff", "slides+cuts"])
def test_a_trimmed_block_renders_the_same_notes_written_by_hand(spans, lead):
    """ONE PIECE. The spanned section is a flat cycle (its whole length);
    the by-hand one is the section's own patterns looping as they are (the
    native path, every pattern as long as the section) — and flattened, the
    path the spanned one takes."""
    spanned = parts_of(build(spans, hand=False, lead=lead))
    native = parts_of(build(spans, hand=True, lead=lead))
    assert spanned.flat and spanned.cycle_bars == 4
    assert not native.flat and native.cycle_bars == 4
    got = section_audio(spanned)
    assert np.max(np.abs(got.base)) > 0.05
    same(got, section_audio(native))
    same(got, section_audio(native.flatten()))


@pytest.mark.parametrize("spans", SPANS.values(), ids=SPANS.keys())
def test_a_trimmed_block_chunked_is_the_by_hand_section_in_one_piece(
    spans, monkeypatch
):
    """CHUNKED. The cap at 4 s cuts the 8 s cycle into two 2-bar chunks:
    a block that starts or ends inside a chunk, or runs across the seam."""
    spanned = parts_of(build(spans, hand=False, lead=SLID))
    native = parts_of(build(spans, hand=True, lead=SLID))
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 10.0)
    assert len(section_windows(spanned)) == 1
    whole, ref = section_audio(spanned), section_audio(native)
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert len(section_windows(spanned)) == 2
    chunked = section_audio(spanned)
    same(chunked, whole)
    same(chunked, ref)


def test_a_trimmed_block_through_its_tracks_fx_is_the_by_hand_one():
    """Each stem's chain runs once over the cycle and what rings past it
    (reverb, drive). Flat against flat: the native path folds a loop's FX
    differently, which is not this feature's to change."""
    spans = SPANS["all three"]
    a, b = build(spans, hand=False), build(spans, hand=True)
    for song in (a, b):
        song["tracks"][0]["fx"] = REVERB
        song["tracks"][2]["fx"] = [{"kind": "drive", "params": {"amount": 3.0}}]
    a, b = clean(a), clean(b)
    got = section_audio(parts_of(a))
    assert len(got.tail) > 1000  # the reverb rings on
    same(got, section_audio(parts_of(b).flatten()))


def test_a_trimmed_block_is_silent_outside_its_bars_and_itself_inside():
    """Dry stems: exact zeros before the block; inside, the pattern alone
    — a 1-bar section of it, played twice — shifted to the block's first
    bar; after it only the last note's release."""
    song = build({"lead": (1, 2)}, hand=False, **ONLY_LEAD)
    got = section_audio(parts_of(song))
    assert not np.any(got.base[:BAR])  # bar 1: nothing, exactly
    assert np.max(np.abs(got.base[BAR : 3 * BAR])) > 0.05
    alone = section_audio(parts_of(build({}, hand=False, bars=1, **ONLY_LEAD)))
    assert alone.base.shape == (BAR, 2)
    np.testing.assert_allclose(got.base[BAR:], looped(alone, 2), atol=ATOL)
    assert not np.any(got.base[3 * BAR + SR // 2 :])  # SHORT rings 50 ms
    # the drums stem alone: hits only inside their bars
    drums = build({"drums": (2, 1)}, hand=False, without=("lead", "pad"))
    got = section_audio(parts_of(drums))
    assert not np.any(got.base[: 2 * BAR])
    assert np.max(np.abs(got.base[2 * BAR : 3 * BAR])) > 0.05


def test_no_span_and_a_full_span_are_one_key_and_one_render():
    plain = build({}, hand=False)
    full = build({"lead": (0, 4), "pad": (0, 4), "drums": (0, 4)}, hand=False)
    assert plain == full and "spans" not in full["sections"][0]
    a, b = parts_of(plain), parts_of(full)
    assert section_key(a, clip_root=None) == section_key(b, clip_root=None)
    # ... and a section with a span has another key, as it must
    trimmed = parts_of(build({"lead": (1, 2)}, hand=False))
    assert section_key(trimmed, clip_root=None) != section_key(a, clip_root=None)


def test_a_section_with_a_trimmed_block_is_one_cycle_its_whole_length():
    song = build({}, hand=False)
    sec = song["sections"][0]
    assert sm.cycle_bars(song, sec) == 2  # LINE (2 bars) divides the 4
    assert len(sm.section_timeline(song, sec)) == 2
    sm.set_span(song, "s", "lead", 1, 1)
    assert sm.cycle_bars(song, sec) == 4
    assert len(sm.section_timeline(song, sec)) == 1
    assert sm.section_seconds(song, sec) == 8.0  # its length did not move
    # a muted track keeps its block's clock: the cycle stays the section
    song["tracks"][0]["mute"] = True
    assert sm.cycle_bars(song, sec) == 4


def test_a_muted_drums_track_keeps_its_clock_under_a_span():
    song = build({"drums": (1, 2)}, hand=False)
    song["tracks"][2]["mute"] = True
    parts = parts_of(clean(song))
    assert parts.flat and parts.arr.drums == [] and parts.arr.drum_bars == 4


# --- an arp and a sound curve restart at the block's start ---------------------------

ARP = {
    "bars": 1,
    "steps_per_bar": 8,
    "cells": [["A2", 0], ["C3", 3], ["E3", 5]],
    "arp_mode": "up",
}


def test_an_arp_plays_only_inside_its_block_and_restarts_at_its_start(monkeypatch):
    """An arp plays the next pitch of its cycle on EVERY step of the grid,
    notes or not — so a trimmed arp has to be told where it may play
    (`arrange._lane_timeline`'s five-value form), and its phase counts from
    the block's first step."""
    song = build({"lead": (1, 2)}, hand=False, lead=ARP, **ONLY_LEAD)
    parts = parts_of(song)
    assert parts.arp == (("A2", "C3", "E3"), 0, 8, 8, 24)
    got = section_audio(parts)
    assert not np.any(got.base[:BAR])  # silent before the block
    assert not np.any(got.base[3 * BAR + SR // 2 :])  # ... and after its release
    alone = section_audio(
        parts_of(build({}, hand=False, bars=1, lead=ARP, **ONLY_LEAD))
    )
    assert np.max(np.abs(alone.base)) > 0.05
    np.testing.assert_allclose(got.base[BAR:], looped(alone, 2), atol=ATOL)
    # chunked: each chunk gates and phases the arp the same
    monkeypatch.setattr(render, "MAX_LOOP_SECONDS", 4.0)
    assert len(section_windows(parts)) == 2
    same(section_audio(parts), got)
    # an untrimmed arp keeps the three-value form it always had (its key)
    plain = parts_of(build({"drums": (0, 3)}, hand=False, lead=ARP, without=("pad",)))
    assert plain.arp == (("A2", "C3", "E3"), 0, 8)


def test_a_layers_arp_is_gated_too():
    pad_arp = build({"pad": (2, 2)}, hand=False, without=("drums",))
    pad_arp["patterns"]["pad"] = {"id": "pad", "kind": "notes", "name": "pad", **ARP}
    pad_arp = clean(pad_arp)
    (lay,) = parts_of(pad_arp).layers
    assert lay["arp"] == [["A2", "C3", "E3"], 0, 16, 32, 64]  # on the Lead's 16ths
    pad_arp["tracks"][0]["mute"] = True
    got = section_audio(parts_of(clean(pad_arp)))
    assert not np.any(got.base[: 2 * BAR])
    assert np.max(np.abs(got.base[2 * BAR :])) > 0.05


def test_the_main_lanes_sound_curve_restarts_with_the_block():
    """A curve drawn on the track's sound runs over one loop of its
    pattern; under a span it starts again at the block's first bar."""
    curve = [[0.0, 200.0], [1.0, 4000.0]]
    span = build({"lead": (1, 2)}, hand=False, **ONLY_LEAD)
    one = build({}, hand=False, bars=1, **ONLY_LEAD)
    for song in (span, one):
        song["tracks"][0]["curves"] = {"cutoff": curve}
    parts = parts_of(clean(span))
    xs = [x for x, _v in parts.curves["cutoff"]]
    assert xs == sorted(xs) and xs[0] == 0.0 and xs[-1] == 1.0
    got, alone = section_audio(parts), section_audio(parts_of(clean(one)))
    # (a curve is sampled per note over the cycle it is drawn on: 1e-6)
    np.testing.assert_allclose(got.base[BAR:], looped(alone, 2), atol=1e-6)
    flat = section_audio(parts_of(build({"lead": (1, 2)}, hand=False, **ONLY_LEAD)))
    assert np.max(np.abs(got.base - flat.base)) > 1e-3  # the curve is heard


# --- through the service: repeats and tails, lanes, long audio, solo, stems, MIDI ----


def two(spans: dict, *, hand: bool, **more) -> dict:
    """The built section, twice (`repeats` 2), then an Outro playing the
    Riff: the spanned section's own tail folds into its second pass and
    into the Outro's head."""
    song = build(spans, hand=hand, **more)
    song["sections"][0]["repeats"] = 2
    song["patterns"]["out"] = {"id": "out", "kind": "notes", "name": "out", **RIFF}
    song["sections"].append(
        {"id": "o", "name": "O", "bars": 1, "cells": {"lead": "out"}}
    )
    return clean(song)


def svc_of(client):
    return client.app.state.svc


def test_repeats_and_tails_are_the_by_hand_songs(client):
    """REPEATS. Each pass replays the trimmed block; the Pad rings (0.7 s)
    past the section's end into its next pass and into the Outro."""
    spans = SPANS["all three"]
    svc = svc_of(client)
    a = svc._song_raw(two(spans, hand=False))
    b = svc._song_raw(two(spans, hand=True))
    assert a.shape == b.shape == (9 * BAR, 2)
    np.testing.assert_allclose(a, b, atol=ATOL)
    assert np.max(np.abs(a)) > 0.05
    # the second pass is not the first (a tail folded in): the test bites
    assert np.max(np.abs(a[: 4 * BAR] - a[4 * BAR : 8 * BAR])) > 1e-4
    assert svc.song_gain(two(spans, hand=False)) == pytest.approx(
        svc.song_gain(two(spans, hand=True)), abs=1e-9
    )


def test_the_export_is_the_plans_wavs_stitched(client):
    """The plan == the export for a song with a trimmed block, as for any."""
    song = two(SPANS["main late, layer early"], hand=False)
    client.app.state.studio.song = song
    client.app.state.studio.fx_master = []
    plan = client.get("/song/plan.json").json()
    first = plan["sections"][0]
    assert (first["cycle_bars"], first["passes"]) == (4, 2)

    def pcm(data: bytes) -> np.ndarray:
        with wave.open(io.BytesIO(data)) as w:
            raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.int32)

    played = []
    for sec in plan["sections"]:
        head, loop = (
            pcm(client.get(sec["url"]).content),
            pcm(client.get(sec["loop_url"]).content),
        )
        played += [head] + [loop] * (sec["passes"] - 1)
    exported = pcm(client.get("/export/song.wav").content)
    played = np.concatenate(played)
    assert exported.shape == played.shape
    assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
    assert np.max(np.abs(exported)) > 1000


def lane_on(song: dict, tid: str, target: str, points) -> dict:
    song["sections"][0]["auto"] = {tid: [{"target": target, "points": points}]}
    return clean(song)


@pytest.mark.parametrize(
    "tid, target, points",
    [
        ("pad", "gain", [[0.0, 0.2], [1.0, 0.9]]),
        ("lead", "cutoff", [[0.0, 0.1], [0.5, 0.9], [1.0, 0.3]]),
        ("pad", "cutoff", [[0.0, 0.8], [1.0, 0.2]]),
    ],
    ids=["a level lane", "a field lane on the main lane", "a field lane on a layer"],
)
def test_under_an_automation_lane_it_is_the_by_hand_song(client, tid, target, points):
    """WITH A LANE. The lane runs across one pass of the SECTION, not the
    block; both songs are flat cycles, so their tracks' FX compare too."""
    spans = SPANS["all three"]
    svc = svc_of(client)
    songs = []
    for hand in (False, True):
        song = two(spans, hand=hand)
        song["tracks"][1]["fx"] = REVERB
        songs.append(lane_on(song, tid, target, points))
    assert parts_of(songs[0]).flat and parts_of(songs[1]).flat
    a, b = svc._song_raw(songs[0]), svc._song_raw(songs[1])
    np.testing.assert_allclose(a, b, atol=ATOL)
    plain = svc._song_raw(clean(two(spans, hand=False)))
    assert np.max(np.abs(a - plain)) > 1e-3  # the lane (and the reverb) is heard


def test_under_a_whole_stem_it_is_the_by_hand_song(client, tmp_path):
    """WITH A LONG ITEM UNDER IT. `pass_cuts` walks sections and passes; a
    span changes neither, so each pass plays the stem's next slice and the
    trimmed block over it."""
    from tests.studio.test_song_long import make_source, place

    sid = make_source(tmp_path, seconds=20)
    svc = svc_of(client)
    songs = []
    for hand in (False, True):
        song = two(SPANS["all three"], hand=hand)
        song["tracks"][3]["id"] = "ta"  # `place` puts the stem on "ta"
        song = clean(song)
        place(song, tmp_path, sid, at_bar=1)
        songs.append(song)
    assert sm.pass_cuts(songs[0]) == sm.pass_cuts(songs[1])
    assert sorted(sm.pass_cuts(songs[0])) == [0, 1]  # both sections, every pass
    a, b = svc._song_raw(songs[0]), svc._song_raw(songs[1])
    np.testing.assert_allclose(a, b, atol=ATOL)
    bare = svc._song_raw(two(SPANS["all three"], hand=False))
    assert np.max(np.abs(a - bare)) > 1e-3  # the stem is in it


def test_solo_this_cell_is_the_by_hand_cell(client):
    """SOLO CELL: the section with only that track, looped."""
    spans = SPANS["all three"]
    svc = svc_of(client)
    for tid in ("lead", "pad", "drums"):
        got = [
            svc.section_result(
                song_play.solo_song(two(spans, hand=hand), tid), 0, loop=True
            ).audio()
            for hand in (False, True)
        ]
        assert np.max(np.abs(got[0])) > 0.01, tid
        # (the route's WAV, decoded: one step of its 16 bits)
        np.testing.assert_allclose(got[0], got[1], atol=1.01 / 32767, err_msg=tid)


def test_the_stems_are_the_by_hand_stems_and_line_up_with_the_mix(client):
    """STEMS: one per track, each the whole song with the others muted — a
    muted track keeps its block's clock, so every stem has the mix's
    length and they sum to it."""
    spans = SPANS["all three"]
    svc = svc_of(client)
    a = dict(svc.song_stems(two(spans, hand=False)))
    b = dict(svc.song_stems(two(spans, hand=True)))
    assert sorted(a) == sorted(b) == ["drums.wav", "synth-1.wav", "synth-2.wav"]
    for name in a:
        assert a[name].shape == (9 * BAR, 2)
        np.testing.assert_allclose(a[name], b[name], atol=1e-8, err_msg=name)
    mix = svc.song_mixdown(two(spans, hand=False))
    np.testing.assert_allclose(sum(a.values()), mix, atol=1e-6)


def _notes_of(song: dict) -> list:
    file = midi_bridge.song_file(song)
    return [
        sorted(
            (round(n.start, 6), round(n.length, 6), n.pitch, n.velocity)
            for n in t.notes
        )
        for t in file.tracks
    ]


def test_the_midi_file_is_the_by_hand_one_and_has_no_note_outside_the_block():
    """MIDI: the export reads the same schedule the audio renders."""
    spans = {"lead": (1, 2), "pad": (2, 2), "drums": (0, 3)}
    a, b = _notes_of(two(spans, hand=False)), _notes_of(two(spans, hand=True))
    assert a == b and any(a)
    lead = _notes_of(two({"lead": (1, 2)}, hand=False, without=("pad", "drums")))[0]
    in_first = [n for n in lead if n[0] < 16.0]  # the first pass: 4 bars = 16 beats
    assert in_first and all(4.0 <= n[0] < 12.0 for n in in_first)
    # a trimmed arp writes nothing outside its block either
    arp = _notes_of(
        build({"lead": (1, 2)}, hand=False, lead=ARP, without=("pad", "drums"))
    )[0]
    assert len(arp) == 16 and all(4.0 <= n[0] < 12.0 for n in arp)


def test_a_trimmed_section_too_long_for_one_cycle_is_refused_naming_the_block(client):
    """A trimmed block makes its section one flat cycle, so it must fit
    MAX_SECTION_SECONDS — as a take or a lane does."""
    song = build({}, hand=False, bars=64)  # 64 bars at 120 bpm: 128 s
    song["bpm"] = 100.0  # 153.6 s; its 2-bar cycle is fine
    song = clean(song)
    song_play.check_length(song)
    sm.set_span(song, "s", "lead", 1, 2)
    with pytest.raises(
        render.RenderError, match="a trimmed block plays it as one pass"
    ):
        song_play.check_length(song)


# === the route: POST /song/cell/span ================================================


def span_song() -> dict:
    """The fixture song with 4-bar sections (Intro: Beat + Riff; Verse:
    Beat + Riff + Hook, twice)."""
    song = song_dict()
    song["sections"][0]["bars"] = 4
    song["sections"][1]["bars"] = 4
    return song


def post_span(client, **data):
    data = {k: str(v) for k, v in data.items()}
    return client.post("/song/cell/span", data=data, headers=HX)


def trigger(r) -> dict:
    return json.loads(r.headers["HX-Trigger"])


def undo_says(client) -> str | None:
    return client.app.state.studio.song_undo_state()["undo"]


@pytest.fixture
def spans(client):
    use_song(client, span_song())
    return client


def test_the_route_sets_a_span_and_names_the_step(spans):
    r = post_span(spans, section_id="s1", track_id="t1", at=1, len=2)
    assert r.status_code == 200, r.text[:200]
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [1, 2]}
    # the Intro's audio, and the head of the Verse its tail rings into
    assert trigger(r)["song-changed"]["sections"] == ["s1", "s2"]
    assert undo_says(spans) == "trim block"
    assert 'id="song-arranger"' in r.text  # the playlist redraws the block

    def close_step():
        """An undo and a redo close the step a gesture was joining (posts
        of one cell within a second are one step)."""
        spans.post("/song/undo", headers=HX)
        spans.post("/song/redo", headers=HX)

    close_step()
    r = post_span(spans, section_id="s1", track_id="t1", at=2, len=2)
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [2, 2]}
    assert undo_says(spans) == "slide block"
    close_step()
    r = post_span(spans, section_id="s1", track_id="t1", at=1, len=3)
    assert undo_says(spans) == "extend block"
    # a span that covers the section clears it ("Fill section")
    r = post_span(spans, section_id="s1", track_id="t1", at=0, len=4)
    assert r.status_code == 200 and "spans" not in song_of(spans)["sections"][0]
    # ... and held to the section: nothing is ever out of range
    post_span(spans, section_id="s1", track_id="t1", at=9, len=9)
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [3, 1]}


def test_the_drawers_from_and_to_are_one_based_and_inclusive(spans):
    r = post_span(spans, section_id="s1", track_id="t1", base=1, **{"from": 2, "to": 3})
    assert r.status_code == 200
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [1, 2]}  # bars 2-3
    post_span(spans, section_id="s1", track_id="t1", base=1, **{"from": 4, "to": 1})
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [3, 1]}  # at least a bar
    post_span(spans, section_id="s1", track_id="t1", base=1, **{"from": 1, "to": 4})
    assert "spans" not in song_of(spans)["sections"][0]


def test_held_alt_arrows_are_one_undo_step(spans):
    """The key posts once a press; within the ring's coalescing window the
    posts of one cell join (`key="span:<section>:<track>"`)."""
    post_span(spans, section_id="s1", track_id="td", at=0, len=2)
    s = spans.app.state.studio
    before = s.song_undo_state()
    for at in (1, 2):
        post_span(spans, section_id="s1", track_id="td", at=at, len=2)
    assert song_of(spans)["sections"][0]["spans"] == {"td": [2, 2]}
    client_post = spans.post("/song/undo", headers=HX)
    assert client_post.status_code == 200
    assert "spans" not in song_of(spans)["sections"][0]  # all three, one step
    assert before["undo"] == "trim block"
    # another cell is another gesture
    post_span(spans, section_id="s1", track_id="td", at=1, len=1)
    post_span(spans, section_id="s1", track_id="t1", at=1, len=1)
    spans.post("/song/undo", headers=HX)
    assert song_of(spans)["sections"][0]["spans"] == {"td": [1, 1]}


@pytest.mark.parametrize(
    "data, said",
    [
        ({"section_id": "s1", "track_id": "t2", "at": 1, "len": 1}, "empty"),
        ({"section_id": "s1", "track_id": "ta", "at": 1, "len": 1}, "empty"),
        ({"section_id": "zz", "track_id": "t1", "at": 1, "len": 1}, "No such section"),
        ({"section_id": "s1", "track_id": "zz", "at": 1, "len": 1}, "No such track"),
        ({"section_id": "s1", "track_id": "t1", "at": "x", "len": 1}, "whole number"),
        ({"section_id": "s1", "track_id": "t1", "at": 1}, "whole number"),
        (
            {"section_id": "s1", "track_id": "t1", "base": 1, "from": "?", "to": 2},
            "whole",
        ),
        (
            {"section_id": "s1", "track_id": "t1", "to_section": "zz", "to_len": 1},
            "No such",
        ),
        ({"section_id": "s1", "track_id": "t1", "to_section": "s2"}, "whole number"),
        ({}, "No such section"),
    ],
)
def test_a_span_that_cannot_be_set_is_a_toast_and_changes_nothing(spans, data, said):
    before = json.dumps(song_of(spans))
    r = post_span(spans, **data)
    assert r.status_code == 422 and said in r.text
    assert json.dumps(song_of(spans)) == before


def test_a_spill_carries_the_block_on_and_says_how_far(client):
    use_song(client, spill_song())
    post_span(client, section_id="a", track_id="t1", at=1, len=1)
    r = post_span(client, section_id="a", track_id="t1", to_section="e", to_len=2)
    assert r.status_code == 200
    assert _blocks(song_of(client)) == [
        ("p1", (1, 1)),
        ("p1", (0, 2)),
        ("p1", (0, 2)),
        ("p3", (0, 2)),
        (None, None),
    ]
    assert trigger(r)["studio-toast"]["message"] == "Riff now runs on through B and C."
    assert undo_says(client) == "extend block"
    assert set(trigger(r)["song-changed"]["sections"]) >= {"b", "c"}
    client.post("/song/undo", headers=HX)  # one step takes all of it back
    assert _blocks(song_of(client))[1] == (None, None)


def test_a_spill_that_cannot_start_says_why(client):
    song = spill_song()
    song["sections"][1]["cells"] = {"t1": "p3"}  # B plays another pattern
    use_song(client, song)
    post_span(client, section_id="a", track_id="t1", at=0, len=1)
    r = post_span(client, section_id="a", track_id="t1", to_section="b", to_len=1)
    assert r.status_code == 200
    assert "runs to the end of A" in trigger(r)["studio-toast"]["message"]
    assert _blocks(song_of(client))[:2] == [("p1", (0, 2)), ("p3", (0, 2))]


def test_a_section_cannot_end_at_or_before_a_blocks_start(spans):
    post_span(spans, section_id="s1", track_id="t1", at=2, len=2)
    r = spans.post("/song/section/s1/bars", data={"bars": "2"}, headers=HX)
    assert r.status_code == 422
    assert "can&#39;t end before its block Riff, which starts at bar 3" in r.text
    assert song_of(spans)["sections"][0]["bars"] == 4
    # one bar of it left: the section shortens and the block is cut to fit
    r = spans.post("/song/section/s1/bars", data={"bars": "3"}, headers=HX)
    assert r.status_code == 200
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [2, 1]}
    # growing a section leaves a trimmed block where it is
    spans.post("/song/section/s1/bars", data={"bars": "8"}, headers=HX)
    assert song_of(spans)["sections"][0]["spans"] == {"t1": [2, 1]}


def test_moving_a_cell_carries_its_span_and_still_obeys_the_lanes_rule(spans):
    post_span(spans, section_id="s2", track_id="t2", at=2, len=2)  # Hook, bars 3-4
    spans.post(
        "/song/cell/auto",
        data={"section_id": "s2", "track_id": "t2", "op": "add", "target": "gain"},
        headers=HX,
    )
    assert "t2" in song_of(spans)["sections"][1]["auto"]
    move = {"from_section": "s2", "from_track": "t2", "section_id": "s1"}
    r = spans.post("/song/cell/move", data=move | {"track_id": "t2"}, headers=HX)
    assert r.status_code == 200
    intro, verse = song_of(spans)["sections"]
    assert intro["spans"] == {"t2": [2, 2]} and "spans" not in verse
    assert "t2" in intro["auto"] and "auto" not in verse  # along a track: they go
    assert undo_says(spans) == "move cell"
    spans.post("/song/undo", headers=HX)
    assert song_of(spans)["sections"][1]["spans"] == {"t2": [2, 2]}
    # a clear drops it; a copy (set_cell on a trimmed cell) keeps the place
    spans.post(
        "/song/cell",
        data={"section_id": "s2", "track_id": "t2", "pattern_id": "p3"},
        headers=HX,
    )
    assert song_of(spans)["sections"][1]["spans"] == {"t2": [2, 2]}
    spans.post(
        "/song/cell",
        data={"section_id": "s2", "track_id": "t2", "pattern_id": ""},
        headers=HX,
    )
    assert "spans" not in song_of(spans)["sections"][1]


def test_a_span_past_the_section_cap_is_refused_with_the_usual_toast(client):
    """A trimmed block makes its section one flat cycle: over 120 s it is
    refused, and the song is as it was."""
    song = build({}, hand=False, bars=64)
    song["bpm"] = 100.0  # 153.6 s of section, a 2-bar cycle
    use_song(client, song)
    before = json.dumps(song_of(client))
    r = post_span(client, section_id="s", track_id="lead", at=1, len=2)
    assert r.status_code == 422
    assert "a trimmed block plays it as one pass" in r.text and "120s" in r.text
    assert json.dumps(song_of(client)) == before


def test_open_in_perform_says_the_loop_has_no_trimmed_blocks(spans):
    r = spans.post("/song/section/s1/to-loop", headers=HX)
    assert r.status_code == 200 and "HX-Trigger" not in r.headers  # nothing to say
    post_span(spans, section_id="s1", track_id="t1", at=1, len=2)
    r = spans.post("/song/section/s1/to-loop", headers=HX)
    assert r.status_code == 200 and "HX-Location" in r.headers
    said = trigger(r)["studio-toast"]["message"]
    assert "no trimmed blocks" in said and "1 block of this section" in said
    # the loop holds the whole pattern (it has no spans)
    arr = spans.app.state.studio.arrangement
    assert sorted(map(tuple, arr.cells)) == [("A1", 0), ("E2", 4)]


# === recording into a trimmed block ================================================


def test_a_take_counts_from_the_blocks_start_and_drops_what_falls_outside(spans):
    """Riff (1 bar of 16ths) plays bars 3-4 of the 4-bar Intro. At 120 bpm a
    bar is 2 s: a note at 4.5 s is a quarter into the block's first bar; a
    note at 1.0 s is in bar 1, where the block is silent."""
    post_span(spans, section_id="s1", track_id="t1", at=2, len=2)
    events = [
        {"midi": 57, "t": 4.5, "vel": 0.7},  # bar 3, beat 2: step 4
        {"midi": 60, "t": 7.25, "vel": 0.6},  # bar 4: the pattern again, step 10
        {"midi": 62, "t": 1.0, "vel": 1.0},  # bar 1: outside
        {"midi": 64, "t": 3.99, "vel": 1.0},  # snaps onto bar 3's first step
        {"midi": 65, "t": 7.99, "vel": 1.0},  # snaps onto the NEXT pass's bar 1
    ]
    data = {"events": json.dumps(events), "take": "tk1", "t0": "0", "t1": "8"}
    data |= {"section_id": "s1", "track_id": "t1"}
    r = spans.post("/song/pattern/p1/record", data=data, headers=HX)
    assert r.status_code == 200, r.text[:200]
    cells = {tuple(c) for c in song_of(spans)["patterns"]["p1"]["cells"]}
    assert cells == {("A1", 0), ("E2", 4), ("A3", 4), ("C4", 10), ("E4", 0)}
    said = trigger(r)["studio-toast"]["message"]
    assert said == "2 notes fell outside the block (bars 3-4) and were not recorded."
    # an untrimmed cell records every note, as before, and says nothing
    r = spans.post(
        "/song/pattern/pd/record", data=data | {"track_id": "td"}, headers=HX
    )
    assert "notes fell outside" not in r.headers.get("HX-Trigger", "")


# === the page =======================================================================


def _arranger(client) -> str:
    html = client.get("/view/song").text
    start = html.rindex("<section", 0, html.index('id="song-arranger"'))
    return html[start : html.index("</section>", html.index("arr-foot", start))]


def _cell(html: str, sid: str, tid: str) -> str:
    start = html.index(f'id="cell-{sid}-{tid}"')
    return html[html.rindex("<td", 0, start) : html.index("</td>", start)]


def test_a_trimmed_block_is_drawn_at_its_bars_with_its_echoes(spans):
    post_span(spans, section_id="s2", track_id="t1", at=1, len=2)
    cell = _cell(_arranger(spans), "s2", "t1")
    head = cell[: cell.index(">")]
    assert 'class="arr-cell filled trimmed"' in head
    assert 'data-at="1" data-len="2"' in head and "--at: 1; --len: 2" in head
    # Riff is 1 bar: its thumbnail twice across the block's 2 bars
    assert 'viewBox="0 0 2.0 1"' in cell and cell.count('<use href="#thumb-p1"') == 2
    assert cell.count('<i class="echo"') == 1  # the Verse plays twice
    assert "Verse, Bass: Riff, bars 2 to 3" in cell
    # every block has its two edges; an empty cell has none
    assert cell.count('class="blk-edge blk-edge-') == 2
    full = _cell(_arranger(spans), "s1", "t1")
    assert 'class="arr-cell filled"' in full and 'data-at="0" data-len="4"' in full
    assert full.count('class="blk-edge blk-edge-') == 2 and "bars 1 to" not in full
    assert "blk-edge" not in _cell(_arranger(spans), "s1", "t2")


def _drawer(client, sid: str, tid: str) -> str:
    r = client.post(
        "/song/drawer", data={"section_id": sid, "track_id": tid}, headers=HX
    )
    assert r.status_code == 200
    html = client.get("/view/song").text
    start = html.rindex("<section", 0, html.index('id="song-drawer"'))
    return html[start : html.index('id="song-thumbs"', start)]


def test_the_drawer_says_where_the_block_plays_and_edits_it(spans):
    plain = _drawer(spans, "s1", "t1")
    head = plain[: plain.index(">")]
    assert "data-at" not in head and "data-len" not in head
    assert "plays all of Intro" in plain
    assert 'name="from" min="1" max="4" step="1" value="1"' in plain
    assert 'name="to" min="1" max="4" step="1" value="4"' in plain
    assert 'class="drawer-span" hx-post="/song/cell/span" hx-trigger="change"' in plain
    assert '<input type="hidden" name="base" value="1">' in plain
    post_span(spans, section_id="s1", track_id="t1", at=2, len=2)
    trimmed = _drawer(spans, "s1", "t1")
    head = trimmed[: trimmed.index(">")]
    assert 'data-at="2" data-len="2"' in head  # where the playheads count from
    assert "plays bars 3–4 of Intro" in trimmed
    assert 'name="from" min="1" max="4" step="1" value="3"' in trimmed
    fill = trimmed[trimmed.index("drawer-fill") - 60 :].split("</button>")[0]
    assert 'hx-post="/song/cell/span"' in fill and "Fill section" in fill
    assert '"at": "0"' in fill and '"len": "4"' in fill
    # a pool pattern's drawer, and an empty cell's, have no such row
    spans.post("/song/drawer", data={"pattern_id": "p3"}, headers=HX)
    assert "drawer-span" not in spans.get("/view/song").text
    assert "drawer-span" not in _drawer(spans, "s1", "t2")


def test_the_drawers_watch_follows_the_open_cells_span(spans):
    from synthlab.studio.service import REGIONS

    _drawer(spans, "s1", "t1")
    watch = REGIONS["song-drawer"].watch
    before = watch(spans.app.state.svc, "song")
    r = post_span(spans, section_id="s1", track_id="t1", at=1, len=1)
    assert watch(spans.app.state.svc, "song") != before
    assert 'id="song-drawer"' in r.text and "plays bars 2–2 of Intro" in r.text
