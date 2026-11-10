"""Synths became a sound bench: no note grid, a fixed reference phrase, and
typing rows that stack into octaves instead of duplicating each other.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.audition import PHRASES, listening_root, phrase_arrangement
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement

CHORDS = Path("synthlab/studio/static/chords.js")
PERFORM = {"HX-Current-URL": "http://testserver/view/perform"}


def test_the_grid_lives_in_perform_and_the_bench_does_not_sequence(tmp_path):
    client = TestClient(create_app(tmp_path))
    bench, stage = client.get("/").text, client.get("/view/perform").text
    assert 'name="cell.A1.0"' in stage and 'name="cell.A1.0"' not in bench
    assert 'id="perform-arrange"' in stage and 'id="perform-arrange"' not in bench
    # ...but the key stays on the bench: the pads and keyboard are built from it
    assert 'name="root"' in bench and 'name="scale_name"' in bench
    assert 'name="phrase"' in bench


def test_bench_edits_never_disturb_the_pattern(tmp_path):
    """The whole point of the split. A patch post carries no cell.*/vel.*, and
    `arrangement_from_form` rebuilds from scratch — without per-lane
    preservation every slider nudge would wipe the Perform grid."""
    with TestClient(create_app(tmp_path)) as client:
        client.post(
            "/fragment/arrangement",
            data={
                "bpm": "150",
                "bars": "2",
                "steps": "16",
                "root": "A1",
                "scale_name": "pentatonic_minor",
                "cell.A1.5": "on",
                "vel.5": "0.4",
                "slide.5": "on",
            },
            headers=PERFORM,
        )
        client.post("/fragment/patch", data={"cutoff": "800", "root": "A1"})
        arr = client.app.state.studio.arrangement
    assert arr.cells == [["A1", 5]], arr.cells
    assert arr.velocities == {"5": 0.4} and arr.slides == [5]
    assert (arr.bars, arr.steps_per_bar, arr.bpm) == (2, 16, 150.0)


def test_each_phrase_renders_something_different(tmp_path):
    """A reference phrase you cannot hear apart is not a reference."""
    base = Arrangement()
    seen = {
        name: tuple(map(tuple, phrase_arrangement(base, name).cells))
        for name in PHRASES
    }
    assert len(set(seen.values())) == len(PHRASES), seen
    for name, arr in ((n, phrase_arrangement(base, n)) for n in PHRASES):
        assert arr.cells, name
        assert arr.drums == [] and arr.bars == 1  # Synths renders synth only

    with TestClient(create_app(tmp_path)) as client:
        urls = set()
        for name in PHRASES:
            r = client.post(
                "/fragment/arrangement",
                data={
                    "phrase": name,
                    "bpm": "124",
                    "root": "A1",
                    "scale_name": "pentatonic_minor",
                },
            )
            assert r.status_code == 200, name
            urls.add(r.headers["HX-Trigger"])
        assert len(urls) == len(PHRASES), "phrases collapsed to one render"


def test_stacked_typing_rows_tile_through_the_surface():
    """Rows on one surface are ONE instrument. They used to lift a whole
    octave each and replay the *same* pads — chosen when there were ~11 note
    pads, too few to slice. There are 15 now, so that stranded pads 11-15 and
    left 5 keys dead on the 7-pad chord row. Rows now rank bottom-to-top and
    each carries on from where the row below stopped, wrapping an octave up
    past the last pad so nothing is dead and nothing is unreachable."""
    js = CHORDS.read_text(encoding="utf-8")
    assert 'const ROW_ORDER = ["low", "home", "top", "num"]' in js
    # a running key-count start index, not a flat rank * 12 lift
    assert "const startOf = (r) =>" in js
    assert "const semiOffset = (r) => Math.max(0, rank(r)) * 12;" not in js
    assert "n + ROWS[x].codes.length" in js
    # wrap past the last pad lifts rather than going dead, by the pad list's
    # own span — the note pads run A3..A5, so a flat +12 fell an octave
    assert "pads[((abs % len) + len) % len]" in js
    assert "Math.floor(abs / len) * lift" in js
    assert "span % 12 === 0" in js
    # the lift has to reach all three trigger paths, not just the piano row
    assert "padNotes(p, lift)" in js and "padOn(p, id, lift)" in js
    assert "i + startOf(r)" in js
    assert "padOffset" not in js  # the old slice-into-the-list scheme is gone


def test_phrase_plays_in_the_listening_register_not_the_bass_root():
    """The grid root is a *bass* root (A1 drove a bassline sequencer). Built
    from it, every phrase was a ~55 Hz chord: it read as static through the
    drive, and ~99% of the energy sat in the bottom FFT bin, so the spectrum
    display had nothing to draw. Phrases follow the chord pads to octave 3.
    """
    assert listening_root("A1") == "A3" and listening_root("C2") == "C3"
    for root in ("A1", "C2", "E2"):
        arr = Arrangement(root=root)
        for name in PHRASES:
            for pitch, _ in phrase_arrangement(arr, name).cells:
                octave = int(pitch.lstrip("ABCDEFG#"))
                assert octave >= 3, f"{name} {root}: {pitch} is below the bench"


def test_phrase_render_sits_above_the_grids_bass_register():
    """The audible symptom, pinned end to end.

    Self-calibrating rather than a magic centroid threshold: render the same
    phrase built the old way (from the grid's bass pitch_rows) and the new way,
    and require a real gap. A fixed threshold passed vacuously on the default
    patch, which is bright enough to survive at A1 — the collapse to ~50 Hz
    only showed on a dark patch (cutoff 150), which is exactly the case that
    sounded like static.
    """
    from dataclasses import replace

    import numpy as np

    from synthlab.analysis.features import extract
    from synthlab.studio.arrange import pitch_rows
    from synthlab.studio.render import RenderInputs, render_audio
    from synthlab.synth import Patch

    base = Arrangement(root="A1")
    spec = PHRASES["hold"]
    rows = pitch_rows(base)  # the bass register the grid is written in
    at_bass = replace(
        base,
        bars=1,
        steps_per_bar=spec["steps"],
        drums=[],
        slides=[],
        velocities={},
        swing=0.0,
        arp_mode="off",
        cells=[[rows[r], step] for r, step in spec["notes"]],
    )
    patch = Patch()
    bench = render_audio(
        RenderInputs(
            patch=patch,
            arr=phrase_arrangement(base, "hold"),
            curves={},
            parts=("synth",),
        )
    )
    bass = render_audio(
        RenderInputs(patch=patch, arr=at_bass, curves={}, parts=("synth",))
    )
    assert np.max(np.abs(bench)) > 0.1  # it makes a sound at all
    assert extract(bench).centroid > extract(bass).centroid * 2, (
        "the phrase is not being lifted out of the grid's bass register"
    )
