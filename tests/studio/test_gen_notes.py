"""Song creation's generators (`studio/gen/`): the chord track and its one
reader, and bass / chords / arp / lead / pad / chops / drums as pure,
seeded functions that write ordinary pattern data.

What is held: a seed writes the same notes in ANOTHER PROCESS (crc32, never
`hash()`); rolling one part leaves the others' cells byte-equal; every note
is on the key's ladder, inside its register, on the grid; the rolling bass
never lands on the kick's step and cuts every repeat; a chord pattern reads
back into the chord track that wrote it; `thin` only removes, and busier
then sparser is no denser than it started."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from synthlab.sequencer import SCALES, midi
from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.gen import build, chordtrack, styles
from synthlab.studio.gen import drums as dr
from synthlab.studio.gen import notes as nt
from synthlab.studio.state import ROOTS

N = schema.nodes()


def _song(root="F#1", scale="minor", degrees=(1, 6, 3, 7), bars=8) -> dict:
    song = sm.new_song(138.0, root, scale)
    song["chords"] = chordtrack.from_degrees(degrees, bars)
    return schema.conform(song, N.SONG)


def _ctx(song=None, register="bass", seed=7, role="", kick=(0, 4, 8, 12), bars=8):
    return nt.context(
        song or _song(),
        None,
        bars=bars,
        kick=kick,
        register=register,
        seed=seed,
        role=role,
    )


def _lanes(seed: int) -> dict:
    """One lane of every kind (and every figure), for one seed."""
    song = _song()
    out = {}
    for kind in nt.GENERATORS:
        ctx = _ctx(song, nt.KIND_REGISTER[kind], seed, role=kind)
        out[kind] = nt.generate(kind, ctx)
    for fig in nt.FIGURES:
        out[f"roll:{fig}"] = nt.bass_rolling(_ctx(song, "bass", seed, role="bass"), fig)
    for mode in nt.ARP_MODES:
        out[f"arp:{mode}"] = nt.arp(_ctx(song, "arp", seed, role="arp"), mode)
    for rhythm in nt.RHYTHMS:
        out[f"chords:{rhythm}"] = nt.chords(
            _ctx(song, "chords", seed, role="c"), rhythm
        )
    out["build"] = dr.build(8, seed)
    out["fill"] = dr.fill(dr.beat("trance", seed, 8), seed, 1.0, "roll")
    out["beat"] = dr.beat("two-step", seed, 4, density=0.8)
    return out


# --- the chord track -----------------------------------------------------------


def test_the_chord_track_resolves_every_step_once_and_tiles_from_bar_one():
    song = _song(degrees=(1, 6, 3, 7), bars=8)
    got = chordtrack.resolve(song, None, 16, 16)
    assert [(c["lo"], c["hi"]) for c in got] == [
        (i * 32, i * 32 + 32) for i in range(8)
    ]
    assert [c["slot"]["deg"] for c in got] == [1, 6, 3, 7, 1, 6, 3, 7]
    # F# minor: i VI III VII are F#m, D, A, E
    assert [c["root"] for c in got[:4]] == [6, 2, 9, 4]
    assert chordtrack.label(song) == "i VI III VII"
    names = [chordtrack.name(song, s) for s in song["chords"]["slots"]]
    assert names == ["F#m", "D", "A", "E"]
    assert chordtrack.chord_at(got, 33)["slot"]["deg"] == 6
    # a section's own track overrides the song's; without one it is the song's
    sec = {"bars": 4, "chords": chordtrack.from_degrees((4, 5), 2)}
    assert [c["slot"]["deg"] for c in chordtrack.resolve(song, sec)] == [4, 5, 4, 5]
    assert chordtrack.track_of(song, {"bars": 4}) is song["chords"]
    assert chordtrack.track_of(sm.new_song(120, "A1", "minor")) is chordtrack.DEFAULT


@pytest.mark.parametrize("root", ROOTS)
def test_every_root_and_every_scale_names_every_degree(root):
    """The dead end the journey hit: two roots, and "VI" refused in a
    five-tone key. Every root works, and a key that is not seven-tone
    counts the degree in its diatonic parent (not the degree modulo its
    length, where "VI" wraps to a second "I")."""
    for scale, steps in SCALES.items():
        song = sm.new_song(120.0, root, scale)
        seven = steps if len(steps) == 7 else chordtrack.parent(steps)
        for deg in range(1, 8):
            for q in sm.CHORD_QUALITIES:
                slot = {"deg": deg, "q": q, "inv": 0}
                tones = chordtrack.semis(song, slot)
                assert tones == sorted(tones) and 0 <= tones[0] < 12
                key = midi(root) % 12
                assert all(
                    (key + t) % 12 in {(key + s) % 12 for s in seven} for t in tones
                )
                assert chordtrack.name(song, slot) and chordtrack.roman(song, slot)
    five = sm.new_song(120.0, root, "pentatonic_minor")
    assert chordtrack.semis(five, {"deg": 6, "q": "triad"}) != chordtrack.semis(
        five, {"deg": 1, "q": "triad"}
    )


@pytest.mark.parametrize(
    "scale,parent",
    [
        ("pentatonic_minor", "minor"),
        ("blues", "minor"),
        ("japanese", "minor"),
        ("pentatonic_major", "major"),
        ("whole_tone", "major"),
    ],
)
def test_a_preset_in_a_key_that_is_not_seven_tone_reads_as_its_parent(scale, parent):
    """A key that is not seven-tone counts a
    chord's degree in its diatonic parent — the same chords, names and
    numerals as the parent key, and the same progressions on the strip's
    menu (`build.progressions`, the lane's and the strip's)."""
    odd, seven = sm.new_song(120.0, "A1", scale), sm.new_song(120.0, "A1", parent)
    for degrees in chordtrack.PRESETS:
        track = chordtrack.from_degrees(degrees, 4)
        assert chordtrack.label(odd, track) == chordtrack.label(seven, track)
        for slot in track["slots"]:
            assert chordtrack.semis(odd, slot) == chordtrack.semis(seven, slot)
            assert chordtrack.name(odd, slot) == chordtrack.name(seven, slot)
    assert [m["label"] for m in build.progressions(odd)] == [
        m["label"] for m in build.progressions(seven)
    ]


def test_a_pentatonic_minor_plays_i_vi_iii_vii_as_written():
    """A pentatonic minor plays A natural minor's i VI III VII — Am F C G —
    on the chord track, in `resolve` (what the generators and the lane
    read) and in a generator's chords; not "I I IV III", the degrees
    wrapped round the five tones."""
    five = sm.new_song(120.0, "A1", "pentatonic_minor")
    five["chords"] = chordtrack.from_degrees((1, 6, 3, 7), 4)
    assert chordtrack.label(five) == "i VI III VII"
    names = [chordtrack.name(five, s) for s in five["chords"]["slots"]]
    assert names == ["Am", "F", "C", "G"]
    got = chordtrack.resolve(five, None, 4, 16)
    assert [c["root"] for c in got] == [9, 5, 0, 7]
    lane = nt.chords(_ctx(five, "chords", bars=4), "held", "close", lead=False)
    bar2 = {midi(p) % 12 for p, st in lane["cells"] if 16 <= st < 32}
    assert bar2 == {5, 9, 0}  # F A C: the VI, its F outside the five tones
    # and a hand-played F major reads back as the VI
    back = chordtrack.from_cells(five, lane)
    assert [x["deg"] for x in back["slots"]] == [1, 6, 3, 7]


def test_chords_read_back_into_the_track_that_wrote_them():
    for degrees, bars in (
        ((1, 6, 3, 7), 8),
        (((1, "7th"), (4, "7th")), 2),
        ((6, 7, 1, 1), 4),
    ):
        song = _song("A1", "minor", degrees, bars)
        lane = nt.chords(_ctx(song, "chords", bars=bars), "held", "close", lead=False)
        back = chordtrack.from_cells(song, lane)
        want = song["chords"]["slots"]
        # two bars of one chord are one chord: merge the track's repeats
        merged = [
            s for i, s in enumerate(want) if i == 0 or s["deg"] != want[i - 1]["deg"]
        ]
        assert [(s["deg"], s["q"]) for s in back["slots"]] == [
            (s["deg"], s["q"]) for s in merged
        ]
        assert [s["at"] for s in back["slots"]] == [s["at"] for s in merged]
        again = nt.chords(
            _ctx(schema.conform({**song, "chords": back}, N.SONG), "chords", bars=bars),
            "held", "close", lead=False,
        )  # fmt: skip
        # the same notes (a chord held over two slots is one chord read back:
        # its second attack is the one thing the track does not hold)
        assert again["cells"] == lane["cells"]
    assert (
        chordtrack.from_cells(_song(), {"bars": 1, "steps_per_bar": 16, "cells": []})
        is None
    )


# --- determinism ---------------------------------------------------------------


def test_a_seed_writes_the_same_notes_in_another_process():
    """crc32, not hash(): PYTHONHASHSEED must not move a note."""
    here = json.dumps(_lanes(11), sort_keys=True)
    code = (
        "import json, sys; sys.path.insert(0, '.');"
        "from tests.studio.test_gen_notes import _lanes;"
        "print(json.dumps(_lanes(11), sort_keys=True))"
    )
    for hashseed in ("0", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": hashseed},
        )
        assert out.stdout.strip() == here
    assert json.dumps(_lanes(12), sort_keys=True) != here


def test_rolling_one_part_leaves_the_others_alone():
    song = _song()
    lead = nt.lead(_ctx(song, "lead", 3, role="lead"))
    arp = nt.arp(_ctx(song, "arp", 3, role="arp"), "random")
    # the bass's seed changes; the lead's and the arp's streams are their own
    assert nt.bass_walk(_ctx(song, "bass", 3, role="bass")) != nt.bass_walk(
        _ctx(song, "bass", 4, role="bass")
    )
    assert nt.lead(_ctx(song, "lead", 3, role="lead")) == lead
    assert nt.arp(_ctx(song, "arp", 3, role="arp"), "random") == arp
    # ... and a part's stream is not another part's with the same seed
    assert (
        nt.rng(_ctx(song, role="a"), "x").random()
        != nt.rng(_ctx(song, role="b"), "x").random()
    )


# --- in key, in register, on the grid ------------------------------------------


@pytest.mark.parametrize(
    "root,scale",
    [
        ("F#1", "minor"),
        ("A0", "dorian"),
        ("E3", "phrygian"),
        ("C2", "pentatonic_minor"),
        ("D2", "major"),
    ],
)
def test_every_note_is_on_the_ladder_inside_the_register_on_the_grid(root, scale):
    """In key — or, in a key that is not seven-tone, a tone of the chord
    under it: its chords are its diatonic parent's, so C
    pentatonic minor's VI, Ab major, plays its Ab."""
    song = _song(root, scale)
    key = midi(root) % 12
    pcs = {(key + s) % 12 for s in SCALES[scale]}
    assert nt.BASS_LOW[0] <= nt.bass_root(song) <= nt.BASS_LOW[1]
    for seed in (1, 2, 3):
        for kind in nt.GENERATORS:
            if kind in nt.SLICE_KINDS:
                continue  # its pitches are slice keys, not the key's tones
            ctx = _ctx(song, nt.KIND_REGISTER[kind], seed, role=kind)
            lane = nt.generate(kind, ctx)
            assert lane["bars"] == 8 and lane["steps_per_bar"] == 16 and lane["cells"]
            for pitch, step in lane["cells"]:
                m = midi(pitch)
                chord = {(key + t) % 12 for t in ctx.chord(step)["tones"]}
                assert m % 12 in pcs or (len(pcs) < 7 and m % 12 in chord), (
                    kind,
                    pitch,
                )
                assert ctx.low <= m <= ctx.low + 24, (kind, pitch, ctx.low)
                assert 0 <= step < 128
            on = {(c[0], c[1]) for c in lane["cells"]}
            assert all((c[0], c[1]) in on for c in lane["cuts"])
            # the pattern is schema-clean as written
            raw = {"id": "aaaaaaaa", "kind": "notes", "name": "x", **lane}
            clean = schema.conform_strict(raw, N.PATTERN_NOTES)
            assert sorted(map(tuple, clean["cells"])) == sorted(
                map(tuple, lane["cells"])
            )
            # and every row is one the drawer shows at that octave
            rows = set(sm.window_rows(song, 2, sm.window_octave(song, lane, 2)))
            assert all(c[0] in rows for c in lane["cells"] if midi(c[0]) % 12 in pcs), (
                kind
            )


# --- the rolling bass ----------------------------------------------------------


@pytest.mark.parametrize("figure", list(nt.FIGURES))
def test_the_rolling_bass_is_never_on_the_kicks_step_and_cuts_every_repeat(figure):
    ctx = _ctx(seed=5, role="bass")
    lane = nt.bass_rolling(ctx, figure)
    ons = nt.note_ons(lane)
    assert not {s for s in ons if s % 4 == 0}  # never the kick's step
    want = {"roll": {1, 2, 3}, "octave": {1, 2, 3}, "gallop": {2, 3}, "offbeat": {2}}[
        figure
    ]
    for beat in range(32):
        assert {s - beat * 4 for s in ons if beat * 4 <= s < beat * 4 + 4} == want
    # each note is one step long: two of one pitch side by side are cut
    assert all(n == 1 for _p, _s, n in nt.runs(lane))
    # on the root of the chord under the beat (a pop lifts a last note)
    for pitch, start, _n in nt.runs(lane):
        root = ctx.chord(start)["root"]
        if midi(pitch) % 12 != root:
            assert figure in ("roll", "gallop") and start % 4 == 3
            tones = ctx.chord(start)["tones"]
            assert midi(pitch) % 12 == (ctx.root + tones[2]) % 12  # the chord's fifth
    if figure == "octave":
        thirds = [midi(p) for p, s, _n in nt.runs(lane) if s % 4 == 3]
        firsts = [midi(p) for p, s, _n in nt.runs(lane) if s % 4 == 1]
        assert [a - b for a, b in zip(thirds, firsts, strict=True)] == [12] * 32


def test_the_bass_pair_is_one_figure_an_octave_apart_from_one_seed():
    ctx = _ctx(seed=9, role="bass")
    mid, sub = nt.bass_pair(ctx, "roll")
    assert nt.note_ons(mid) == nt.note_ons(sub)
    lows = {midi(c[0]) for c in sub["cells"]}
    assert max(lows) < min(midi(c[0]) for c in mid["cells"])
    # the sub stays on the roots: no pops
    assert all(midi(p) % 12 == ctx.chord(s)["root"] for p, s, _n in nt.runs(sub))
    assert nt.bass_pair(ctx, "roll") == (mid, sub)


def test_a_bass_keeps_off_a_two_step_kick_too():
    ctx = _ctx(kick=(0, 10), seed=2, role="bass")
    for figure in nt.FIGURES:
        ons = nt.note_ons(nt.bass_rolling(ctx, figure))
        assert not {s for s in ons if s % 16 in (0, 10)}
    assert not {s for s in nt.note_ons(nt.bass_walk(ctx)) if s % 16 in (0, 10)}


def test_the_other_lines_show_what_their_style_needs():
    song = _song("A1", "minor")
    assert nt.bass_walk(_ctx(song, seed=1))["slides"]  # a slide in the funk bass
    stabs = nt.note_ons(nt.chords(_ctx(song, "chords", 1), "stabs"))
    assert sum(s % 4 == 2 for s in stabs) / len(stabs) >= 0.75  # on the offbeats
    chops = nt.chops(_ctx(song, "vocal", 1))
    assert len({c[0] for c in chops["cells"]}) >= 4
    held = nt.runs(nt.pad(_ctx(song, "pad", 1)))
    assert max(n for _p, _s, n in held) >= 16
    for mode in nt.ARP_MODES:
        lane = nt.arp(_ctx(song, "arp", 1), mode, rate=1)
        assert len(nt.note_ons(lane)) == 128  # a note every sixteenth


# --- derived variants ----------------------------------------------------------


def test_thin_only_removes_and_busier_then_sparser_is_no_denser():
    beat = dr.beat("trance", 3, 8)
    for name in dr.THIN:
        thin = dr.thin(beat, keep=name)
        assert {tuple(h) for h in thin["drums"]} <= {tuple(h) for h in beat["drums"]}
    assert not any(v == "kick" for v, _s in dr.thin(beat, keep="nokick")["drums"])
    busy = dr.busier(beat, 0.3, 1)
    assert {tuple(h) for h in beat["drums"]} < {tuple(h) for h in busy["drums"]}
    back = dr.sparser(busy, 0.3, 1)
    assert len(back["drums"]) <= len(beat["drums"])
    assert {tuple(h) for h in back["drums"]} < {tuple(h) for h in busy["drums"]}
    ctx = _ctx(register="lead", seed=4, role="lead")
    lane = nt.lead(ctx)
    more = nt.busier(ctx, lane, 0.3)
    assert {tuple(c) for c in lane["cells"]} < {tuple(c) for c in more["cells"]}
    less = nt.sparser(ctx, more, 0.3)
    assert {tuple(c) for c in less["cells"]} < {tuple(c) for c in more["cells"]}
    assert len(nt.runs(less)) <= len(nt.runs(lane))


def test_a_fill_leaves_the_bars_before_it_and_a_build_never_thins():
    beat = dr.beat("melodic-techno", 2, 8)
    for length in (0.5, 1.0, 2.0):
        fill = dr.fill(beat, 7, length, "roll")
        edge = 128 - int(length * 16)
        assert [h for h in fill["drums"] if h[1] < edge] == [
            h for h in beat["drums"] if h[1] < edge
        ]
        assert fill["drums"] != beat["drums"]
        vel = [
            fill["drum_vel"][str(s)]
            for s in range(112, 128)
            if str(s) in fill["drum_vel"]
        ]
        assert vel == sorted(vel)  # rising to the bar line
    keep = dr.fill(dr.beat("trance-climax", 2, 8), 7, 0.5, "roll", ("kick", "ohat"))
    assert all(["kick", 112 + k] in keep["drums"] for k in (0, 4, 8, 12))
    roll = dr.build(8, 3)
    per = dr.hits_per_bar(roll, "snare")
    # quarters, eighths, sixteenths, then 32nds by ratchet (a ratcheted step
    # counts as the hits it plays)
    assert per == [4, 4, 8, 8, 16, 16, 32, 32]
    assert dr.hits_per_bar(roll, "kick")[-1] == 0  # the kick thins out
    # the velocity and the tune are the ROLL's (each snare hit's own), not
    # the step's: the kick under it keeps its level
    snare = [roll["hits"][f"snare.{s}"] for v, s in roll["drums"] if v == "snare"]
    vel = [h["v"] for h in snare]
    assert vel == sorted(vel) and vel[0] == 0.4 and vel[-1] == 1.0
    tune = [h.get("t", 0.0) for h in snare]
    assert tune == sorted(tune) and tune[0] == 0.0 and tune[-1] == 7.0
    assert [
        s
        for v, s in roll["drums"]
        if v == "snare" and roll["hits"][f"snare.{s}"].get("r")
    ] == list(range(96, 128))
    assert roll["drum_vel"] == {} and not any(
        k.startswith("kick") for k in roll["hits"]
    )


def test_density_scales_a_beats_sprinkles_and_never_its_groove():
    from synthlab.studio import beatgen

    plain = beatgen.generate("techno", 5, 4)
    assert (
        beatgen.generate("techno", 5, 4, density=0.5) == plain
    )  # the style as written
    assert beatgen.generate("techno", 5, 4, density=None) == plain
    none = beatgen.generate("techno", 5, 4, density=0.0)
    full = beatgen.generate("techno", 5, 4, density=1.0)
    assert len(none) < len(plain) < len(full)
    assert (
        {tuple(h) for h in none}
        <= {tuple(h) for h in plain}
        <= {tuple(h) for h in full}
    )
    assert all(["kick", b * 16 + k] in none for b in range(4) for k in (0, 4, 8, 12))


def test_the_styles_are_whole_and_name_only_what_exists():
    from synthlab.studio import beatgen, recipes
    from synthlab.studio.drumfactory import DRUM_KITS

    assert set(styles.STYLES) == {
        "melodic_techno",
        "filter_house",
        "garage",
        "trance",
        "electro",
        "industrial",
        "psych",
        "sixties",
        "hiphop",
        "dnb",
        "dubstep",
        "synthwave",
        "future_bass",
        "lofi",
        "ambient",
        "chiptune",
        "hardstyle",
        "phonk",
    }
    assert styles.DEFAULT == "melodic_techno"
    for key, st in styles.STYLES.items():
        assert st["bpm_range"][0] <= st["bpm"] <= st["bpm_range"][1]
        assert st["kit"] in DRUM_KITS and st["progressions"] and st["keys"]
        assert all(root in ROOTS and scale in SCALES for root, scale in st["keys"])
        tracks = {t["key"]: t for t in st["tracks"]}
        assert len(tracks) <= 8
        for t in st["tracks"]:
            assert recipes.get_recipe(t["sound"]), t["sound"]
            assert t["role"] in sm.TRACK_ROLES and t["register"] in nt.REGISTER
            assert all(kind in nt.GENERATORS for kind, _p in t["parts"].values())
        for row in st["shape"]:
            assert row["drums"] is None or row["drums"] in st["drums"]
            assert all(
                k in tracks and v in tracks[k]["parts"] for k, v in row["play"].items()
            )
        for pipe in st["drums"].values():
            assert all(op[1] in beatgen.STYLES for op in pipe if op[0] == "beat")
        card = styles.card(key)
        assert card["bars"] == sum(s["bars"] for s in card["sections"])
    # the styles' lengths: 176, 144, 104 and 144 bars (the long trance: 208)
    assert [
        styles.bars_of(k)
        for k in ("melodic_techno", "filter_house", "garage", "trance")
    ] == [176, 144, 104, 144]
    assert styles.bars_of("trance", "long") == 208


# --- the bass switch: call and response between bass sounds -------------------


def _switch(voices=2, every=2.0, seed=7, song=None, bars=8, **kw):
    ctx = _ctx(song, "bass", seed, role="sw", bars=bars)
    return nt.bass_switch(ctx, voices, every, **kw), ctx


def _held(lane: dict) -> set[int]:
    return {s for _p, s in lane["cells"]}


def test_a_bass_switch_never_holds_a_step_in_two_lanes():
    for voices in (2, 3, 4):
        for every in (1.0, 2.0, 3.0):
            lanes, _ = _switch(voices, every)
            assert len(lanes) == voices
            sets = [_held(lane) for lane in lanes]
            for i, a in enumerate(sets):
                for b in sets[i + 1 :]:
                    assert not a & b


def test_every_window_holds_a_note_in_its_own_lane():
    for voices, every in ((2, 2.0), (3, 2.0), (2, 1.0), (4, 1.0)):
        lanes, ctx = _switch(voices, every)
        win = int(every * 4)
        for w in range(ctx.total // win):
            ons = {s for k in range(voices) for s in nt.note_ons(lanes[k])}
            own = nt.note_ons(lanes[w % voices])
            assert any(w * win <= s < (w + 1) * win for s in own), (voices, every, w)
            # and nothing of another voice there
            for k in range(voices):
                if k != w % voices:
                    assert not any(
                        w * win <= s < (w + 1) * win for s in nt.note_ons(lanes[k])
                    )
            assert ons


def test_a_bass_switch_is_seeded_and_the_seed_moves_the_figure():
    a, _ = _switch(seed=7)
    assert a == _switch(seed=7)[0]
    assert any(_switch(seed=s)[0] != a for s in range(8, 14))


def test_one_voice_plays_every_window():
    (lane,), ctx = _switch(1, 2.0)
    ons = nt.note_ons(lane)
    assert all(any(w * 8 <= s < w * 8 + 8 for s in ons) for w in range(ctx.total // 8))


def test_a_bass_switch_plays_the_chord_roots_the_roots_bass_reads():
    lanes, ctx = _switch(2, 2.0)
    for lane in lanes:
        for pitch, start, _n in nt.runs(lane):
            want = nt.above(ctx.low, ctx.chord(start - start % 8)["root"])
            assert midi(pitch) == want


def test_generate_returns_one_lane_a_voice():
    lanes, ctx = _switch(3, 2.0)
    for k in range(3):
        assert nt.generate("bass_switch", ctx, voice=k, voices=3, every=2.0) == lanes[k]


def test_a_bass_switch_can_leave_the_kicks_steps():
    ctx = _ctx(role="sw", kick=(0, 10))
    for k in range(2):
        lane = nt.generate("bass_switch", ctx, voice=k, leave_kick=True)
        assert not {s % 16 for s in nt.note_ons(lane)} & {0, 10}
        assert nt.note_ons(lane)


@pytest.mark.parametrize("meter", ["3/4", "6/8", "7/8"])
def test_a_bass_switch_in_another_meter_stays_inside_its_bars(meter):
    from synthlab.studio import meter as _m
    from synthlab.studio import meterops

    six = _m.parse(meter).sixteenths
    lanes, ctx = _switch(2, 2.0)
    fitted = [meterops.beat_fit(lane, meter) for lane in lanes]
    for lane in fitted:
        for _p, start, n in nt.runs(lane):
            assert start // six == (start + n - 1) // six  # no note crosses a bar
    held = [_held(lane) for lane in fitted]
    assert not held[0] & held[1]
    # the windows restart at each bar: the first voice opens every bar
    for bar in range(ctx.bars):
        first = min(s for lane in fitted for s in nt.note_ons(lane) if s // six == bar)
        assert first in nt.note_ons(fitted[0])


# --- chop stutter: a chopped sampler's slice keys, walked ----------------------


def _stutter(seed=7, bars=8, **kw) -> dict:
    return nt.chop_stutter(_ctx(None, "vocal", seed, role="chop", bars=bars), **kw)


def _ends(bars: int) -> set[int]:
    """The last half beat of every 4-bar phrase (and of the lane)."""
    ends = {min(bars, b + 4) * 16 for b in range(0, bars, 4)}
    return {e - k for e in ends for k in (1, 2)}


def test_chop_stutter_plays_only_the_slice_keys():
    for slices in (8, 16, 32):
        for order in nt.CHOP_ORDERS:
            for repeat in (0.0, 0.3, 1.0):
                lane = _stutter(slices=slices, order=order, repeat=repeat)
                keys = {midi(p) for p, _s in lane["cells"]}
                assert keys <= set(range(nt.CHOP_ROOT, nt.CHOP_ROOT + slices))
                assert lane["bars"] == 8 and lane["steps_per_bar"] == 16
                raw = {"id": "aaaaaaaa", "kind": "notes", "name": "x", **lane}
                assert schema.conform_strict(raw, N.PATTERN_NOTES)["cells"]
    # a walk of 16 at repeat 0 plays every slice key
    keys = {midi(p) for p, _s in _stutter(repeat=0.0)["cells"]}
    assert keys == set(range(nt.CHOP_ROOT, nt.CHOP_ROOT + 16))
    assert nt.CHOP_ROOT == 60  # C4: the sampler's own default root


def test_chop_stutter_is_seeded():
    a = _stutter(seed=3, order="shuffle", repeat=0.4)
    assert a == _stutter(seed=3, order="shuffle", repeat=0.4)
    assert any(_stutter(seed=s, order="shuffle", repeat=0.4) != a for s in (4, 5, 6))
    # forward is a walk, whatever the seed, when nothing stutters
    assert _stutter(seed=1, repeat=0.0) == _stutter(seed=2, repeat=0.0)


def test_reverse_walks_down_and_forward_walks_up():
    for order, step in (("forward", 1), ("reverse", -1)):
        bar = sorted(
            (s, midi(p))
            for p, s in _stutter(order=order, repeat=0.0)["cells"]
            if s < 14
        )
        pitches = [m for _s, m in bar]
        assert all(b - a == step for a, b in zip(pitches, pitches[1:], strict=False))
    assert midi(_stutter(order="reverse", repeat=0.0)["cells"][0][0]) == 75


def test_no_stutter_at_repeat_zero_but_every_phrase_ends_in_one():
    for bars in (8, 6, 1):
        lane = _stutter(repeat=0.0, bars=bars)
        notes = nt.runs(lane)
        ends = _ends(bars)
        for (p0, _s0, _n0), (p1, s1, _n1) in zip(notes, notes[1:], strict=False):
            if s1 not in ends:
                assert p1 != p0, (bars, s1)  # every step moves on
        # ... and the last half beat of each phrase: the slice before, twice
        by = {s: p for p, s, _n in notes}
        for e in sorted(ends)[1::2]:
            assert by[e] == by[e - 1] == by[e - 2], (bars, e)


def test_at_repeat_one_every_bar_still_moves_on():
    for slices in (8, 16, 32):
        lane = _stutter(repeat=1.0, slices=slices)
        notes = nt.runs(lane)
        assert {midi(p) for p, _s, _n in notes} <= set(range(60, 60 + slices))
        firsts = [p for p, s, _n in notes if s % 16 == 0]
        assert len(firsts) == 8  # every bar opens on a note ...
        for a, b in zip(firsts, firsts[1:4], strict=False):  # ... a new slice
            assert a != b
        # and the rest of a bar stutters: never more than two slices a bar
        for bar in range(8):
            held = {p for p, s, _n in notes if bar * 16 <= s < bar * 16 + 16}
            assert len(held) <= 2


def test_some_stutters_at_a_middling_repeat_and_they_are_short():
    lane = _stutter(repeat=0.5)
    notes = nt.runs(lane)
    ends = _ends(8)
    repeats = [
        b
        for a, b in zip(notes, notes[1:], strict=False)
        if b[0] == a[0] and b[1] not in ends
    ]
    assert repeats and all(n == 1 for _p, _s, n in repeats)  # sixteenths


def test_hostile_settings_never_raise():
    for kw in ({"slices": "x"}, {"repeat": "nan"}, {"repeat": float("nan")},
               {"slices": 999}, {"slices": -3}, {"order": "sideways"}):  # fmt: skip
        lane = _stutter(**kw)
        assert lane["cells"]
        assert {midi(p) for p, _s in lane["cells"]} <= set(range(60, 60 + 32))


def test_chop_stutter_is_a_kind_the_write_menu_offers():
    from synthlab.studio.service.songpage import GEN_KINDS

    assert "chop_stutter" in nt.GENERATORS and nt.KIND_REGISTER["chop_stutter"]
    assert "chop_stutter" in dict(GEN_KINDS)
    assert build._KIND_ROLE["chop_stutter"] == "vocal"
    song = _song()
    tid = sm.add_track(song, "synth", "Chops")
    sm.add_section(song, "A", 8)
    (pid,) = build.write(song, tid, "chop_stutter", 5)
    p = song["patterns"][pid]
    assert p["gen"]["kind"] == "chop_stutter" and p["cells"]
    assert {midi(c[0]) for c in p["cells"]} <= set(range(60, 76))
    assert sm._track(song, tid)["role"] == "vocal"


# the kinds there were before chop_stutter: their lanes, three seeds, three
# keys — the digest the code before it wrote (an append must move none)
OLD_KINDS = (
    "bass_rolling", "bass_root", "bass_offbeat", "bass_walk", "bass_2step",
    "bass_switch", "chords", "pad", "arp", "lead", "chops", "hit",
)  # fmt: skip
OLD_DIGEST = "453d4b47d82b263460ff2516cf5f883c5e5e40569e58e57e98f5e031643542b5"


def test_the_existing_generators_write_what_they_wrote():
    out = {}
    for seed in (1, 7, 11):
        for root, scale in (
            ("F#1", "minor"),
            ("C2", "pentatonic_minor"),
            ("D2", "major"),
        ):
            song = _song(root, scale)
            for kind in OLD_KINDS:
                ctx = _ctx(song, nt.KIND_REGISTER[kind], seed, role=kind)
                out[f"{seed}:{root}:{kind}"] = nt.generate(kind, ctx)
    got = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    assert got == OLD_DIGEST
    assert list(nt.GENERATORS)[: len(OLD_KINDS)] == list(OLD_KINDS)
