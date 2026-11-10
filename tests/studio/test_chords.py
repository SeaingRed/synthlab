import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.sequencer import SCALES, midi
from synthlab.studio.chords import (
    VOICINGS,
    CodeError,
    chord_for_midi,
    parse_code,
    scale_chords,
)
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_a_minor_triads_are_the_textbook_set():
    names = [c["name"] for c in scale_chords("A1", "minor")]
    assert names == ["Am", "Bdim", "C", "Dm", "Em", "F", "G"]
    assert scale_chords("A1", "minor")[0]["notes"] == [
        midi("A1"),
        midi("C2"),
        midi("E2"),
    ]


def test_pentatonic_yields_real_triads():
    names = [c["name"] for c in scale_chords("A3", "pentatonic_minor")]
    assert names == ["Am", "C", "D5", "Em", "G5"]


def test_c_major_sevenths():
    names = [c["name"] for c in scale_chords("C2", "major", "7th")]
    assert names[0] == "Cmaj7" and names[4] == "G7" and names[1] == "Dm7"


@pytest.mark.parametrize("scale_name", sorted(SCALES))
@pytest.mark.parametrize("voicing", sorted(VOICINGS))
def test_every_scale_and_voicing_builds_in_scale_chords(scale_name, voicing):
    from synthlab.sequencer import scale

    tones = {m % 12 for m in scale("A1", scale_name, octaves=1)}
    for c in scale_chords("A1", scale_name, voicing):
        assert c["notes"] == sorted(c["notes"])
        assert len(c["notes"]) >= 2
        if voicing in ("triad", "7th", "9th"):
            assert {n % 12 for n in c["notes"]} <= tones


def test_chord_for_midi_snaps_to_nearest_scale_tone():
    assert chord_for_midi(midi("A3"), "A1", "minor") == [
        midi("A3"),
        midi("C4"),
        midi("E4"),
    ]
    # C# is not in A minor: nearest tone is C (below) -> C major
    assert chord_for_midi(midi("C#4"), "A1", "minor") == [
        midi("C4"),
        midi("E4"),
        midi("G4"),
    ]
    assert chord_for_midi(midi("A3"), "A1", "minor", "power") == [
        midi("A3"),
        midi("E4"),
    ]


def test_parse_code_notes_symbols_and_romans():
    assert parse_code("C4 E4 G4", "A1", "minor") == [[60], [64], [67]]
    assert parse_code("Am7 F C G", "A1", "minor")[0] == [57, 60, 64, 67]
    assert (
        parse_code("I V vi IV", "C2", "major")[0]
        == scale_chords("C2", "major")[0]["notes"]
    )
    assert parse_code("i ~ v", "A1", "minor")[1] == []
    assert parse_code("", "A1", "minor") == []


@pytest.mark.parametrize(
    "bad",
    # the last two: a degree of thousands of digits, past what int() converts
    # (its ValueError was no CodeError, so the routes answered 500)
    ["H4", "Cxyz", "bd*4", "Am7 Q", "C" + "9" * 5000, "Cadd" + "1" * 5000],
)
def test_parse_code_errors(bad):
    with pytest.raises(CodeError):
        parse_code(bad, "A1", "minor")


def test_keyboard_carries_chord_sets(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="chord-bar"' in html
    assert "data-chords=" in html
    # default scale is pentatonic_minor: five degree pads, each with notes
    assert html.count('class="chord-pad"') == 5
    # the chord library is NOT in the page — it is ~500 buttons and most of
    # the editor's weight, so it is fetched on first open instead
    assert html.count("data-notes=") == 5
    assert 'hx-get="/fragment/chordlib?key=' in html


def test_chord_library_is_every_category_quality_root():
    from synthlab.studio.chords import CATEGORIES, QUALITIES, chord_library

    cats = chord_library("A3", "minor")
    assert [c["key"] for c in cats] == [k for k, _ in CATEGORIES]
    assert sum(len(c["qualities"]) for c in cats) == len(QUALITIES)
    major = cats[0]["qualities"][0]
    assert major["label"] == "maj"
    assert [p["name"] for p in major["pads"]][:3] == ["C", "C#", "D"]
    assert major["pads"][0]["notes"] == [48, 52, 55]  # C3 E3 G3
    minor = cats[1]["qualities"][0]
    assert minor["pads"][9]["name"] == "Am" and minor["pads"][9]["in_key"]
    assert not major["pads"][9]["in_key"]  # A major is out of A minor
    seventh = {q["label"]: q for q in cats[2]["qualities"]}
    assert seventh["m7"]["pads"][9]["in_key"]  # Am7
    assert not seventh["7"]["pads"][9]["in_key"]  # A7 has C#
    dim = {q["label"]: q for q in cats[5]["qualities"]}
    assert dim["m7b5"]["pads"][11]["in_key"]  # Bø in A minor
    add = {q["label"]: q for q in cats[7]["qualities"]}
    assert add["6/9"]["pads"][0]["name"] == "C6/9"


def test_parse_code_understands_uncommon_names():
    def notes(sym):
        return parse_code(sym, "C2", "major")[0]

    # alternative spellings
    assert notes("CMin7") == notes("Cm7") == notes("C-7") == [48, 51, 55, 58]
    assert notes("Gdom7") == notes("G7")
    assert notes("E△") == notes("E") and notes("E△7") == notes("Emaj7")
    assert notes("A°") == notes("Adim") and notes("Aø") == notes("Am7b5")
    assert notes("F(#5)") == notes("Faug") == [53, 57, 61]
    assert notes("Fno3") == [53, 60]
    assert notes("D-9") == notes("Dm9") and notes("D-11") == notes("Dm11")
    assert notes("CM7") == notes("Cmaj7")
    # slash chords: inversion / doubled root / added bass, always below
    assert notes("E/G#") == [44, 52, 56, 59]
    assert notes("E7/B") == [47, 52, 56, 59, 62]
    assert notes("C/D") == [38, 48, 52, 55]
    assert notes("F/E") == [52, 53, 57, 60]  # the E just under the chord
    assert notes("C/C") == [36, 48, 52, 55]
    assert notes("Cm/C") == [36, 48, 51, 55]
    # sixes, extensions, alterations
    assert notes("C6/9") == notes("C6add9") == [48, 52, 55, 57, 62]
    assert notes("Abmaj7(#11)") == notes("Abmaj7#11")
    assert notes("Dmaj9(add13)") == notes("Dmaj13")
    assert notes("Bm11 add(m2)") == sorted(notes("Bm11") + [59 + 1])  # C above B3
    assert notes("C9b5") == [48, 52, 54, 58, 62]
    assert notes("B#9b5") == [n + 12 for n in notes("C9b5")]  # B#3 is C4
    assert notes("A#9#5") == [58, 62, 66, 68, 72]
    assert notes("F9#11") == [53, 57, 60, 63, 67, 71]
    assert notes("D7b10") == notes("D7#9")
    assert notes("C7alt") == [48, 52, 54, 58, 61, 63]
    assert notes("Gsus4") == [55, 60, 62] and notes("G7sus4") == [55, 60, 62, 65]
    assert notes("Caug7") == notes("C7#5")


def test_harmonic_series_of_root():
    from synthlab.studio.chords import harmonic_notes

    h = harmonic_notes("A1")
    assert [p["name"] for p in h[:6]] == ["A2", "A3", "E4", "A4", "C#5", "E5"]
    assert [p["partial"] for p in h] == list(range(1, 13))
    assert h[6]["name"] == "G5"  # 7th partial ~ flat seventh


def test_key_rows_rendered(tmp_path):
    html = make_client(tmp_path).get("/").text
    for row in ("num", "top", "home", "low"):
        assert f'name="row-{row}"' in html, row
    surfaces = (
        "piano notes chords major minor seventh extended sus dim aug add "
        "altered harmonics off"
    ).split()
    for surface in surfaces:
        assert html.count(f'<option value="{surface}">') == 4, surface
    assert html.count('class="note-pad harm') == 12
    # every mappable pad has a key-hint slot: notes + key chords + harmonics.
    # the library's own hints arrive with it, on first open.
    assert html.count('class="khint"') == 11 + 5 + 12
    js = Path("synthlab/studio/static/chords.js").read_text(encoding="utf-8")
    assert "BracketRight" in js and "Slash" in js and "Equal" in js  # physical codes
    assert 'rows = { num: "chords", top: "piano", home: "piano", low: "off" }' in js


def test_chord_library_pads_rendered(tmp_path):
    """The poster now arrives from its own endpoint on first open."""
    html = make_client(tmp_path).get("/fragment/chordlib").text
    from synthlab.studio.chords import QUALITIES

    assert html.count('class="chord-pad lib') == 12 * len(QUALITIES)
    assert 'aria-label="play Am (in key)"' in html
    assert 'data-degree="dim-0-0"' in html and 'data-cat="altered"' in html
    # each multi-quality category shows one quality row at a time
    assert html.count('class="lib-q-btn"') == len(QUALITIES) - 1  # minor has one
    assert html.count('class="chord-pads lib lib-row" data-q="0" >') == 9


def test_scale_notes_cover_two_octaves_in_key():
    from synthlab.studio.chords import scale_notes

    pads = scale_notes("A3", "minor")
    names = [p["name"] for p in pads]
    assert names[:8] == ["A3", "B3", "C4", "D4", "E4", "F4", "G4", "A4"]
    assert len(pads) == 15 and pads[-1]["name"] == "A5"
    assert [p["degree"] for p in pads[:8]] == [1, 2, 3, 4, 5, 6, 7, 1]
    assert len(scale_notes("A3", "pentatonic_minor")) == 11


def test_note_pads_rendered_under_keyboard(tmp_path):
    """Every tone of the key, octaves 3-4, as big tappable buttons; the
    tonic pads are marked; each carries its MIDI number for chords.js."""
    html = make_client(tmp_path).get("/").text
    assert 'class="note-pads" role="group"' in html
    # default key A pentatonic_minor: 5 per octave x 2 + top root
    assert html.count("data-midi=") == 11 + 12  # scale notes + harmonic pads
    assert html.count('class="note-pad root"') == 3
    assert 'data-midi="57" aria-label="play A3"' in html
    assert 'data-midi="81" aria-label="play A5"' in html


def test_code_route_plays_and_errors(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/chords/code", data={"code": "Am C G"})
    assert r.status_code == 200
    data = r.json()
    assert data["chords"][0] == [57, 60, 64]
    assert data["beat_seconds"] == pytest.approx(60 / 124)
    r = client.post("/fragment/chords/code", data={"code": "nope!"})
    assert r.status_code == 422


def test_code_to_grid_writes_in_row_notes(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    r = client.post("/fragment/chords/grid", data={"code": "A1 C2 E2 A2"})
    assert r.status_code == 200
    steps = {(p, st) for p, st in s.arrangement.cells}
    assert ("A1", 0) in steps and ("C2", 1) in steps and ("E2", 2) in steps
    assert "preview-ready" in r.headers.get("HX-Trigger", "")


def test_code_to_grid_with_nothing_typed_says_what_to_type(tmp_path):
    """→ grid pressed on an empty code line used to answer "that sequence
    is all rests" (a 422)."""
    client = make_client(tmp_path)
    r = client.post("/fragment/chords/grid", data={"code": "  "})
    assert r.status_code == 422 and "Type a progression" in r.text


ISLAND = Path("synthlab/studio/static/chords.js")


def test_rows_tile_through_the_pad_list_instead_of_duplicating_it():
    """Two rows on the same surface used to play the SAME pads an octave
    apart (`semiOffset = rank * 12`), so with 15 note pads and a 10-key row
    pads 11-15 were unreachable at their own pitch. Rows must instead
    continue where the row below left off."""
    src = ISLAND.read_text(encoding="utf-8")
    assert "startOf" in src, "no per-row start index — rows still duplicate pads"
    # the old "rank * 12" lift must be gone as the pad-row mapping
    assert "const semiOffset = (r) => Math.max(0, rank(r)) * 12;" not in src
    # running past the last pad wraps and lifts, rather than going dead
    assert "cycleOf" in src and "Math.floor(abs / len) * lift" in src
    # the lift comes from the pad list's own span, not a hardcoded octave:
    # the note pads run A3..A5, so a flat +12 dropped the row mid-way
    assert "span % 12 === 0" in src and "pads.length - 1" in src


def test_minus_and_equal_shift_the_octave():
    """`-`/`=` are a dedicated octave pair. Z/X already did this but Z is the
    low row's first key, so assigning that row stole octave-down entirely."""
    src = ISLAND.read_text(encoding="utf-8")
    assert "Minus" in src and "Equal" in src
    assert "setOctave" in src, "chords.js must drive piano.js's octave"


def test_number_row_gives_up_minus_and_equal():
    """They cannot both play a pad and shift the octave."""
    src = ISLAND.read_text(encoding="utf-8")
    num = re.search(r"num:\s*\{\s*codes:\s*\"([^\"]+)\"", src)
    assert num, "num row codes not found"
    codes = num.group(1).split()
    assert "Minus" not in codes and "Equal" not in codes
    assert len(codes) == 10, codes
