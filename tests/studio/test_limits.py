"""Bounds on inputs that aren't tied to one existing test file: starred
breed tiles and captured song scenes. Notation,
clip-lane and upload bounds live beside the modules/routes they guard
(test_notation.py, test_clips.py, test_voice.py)."""

from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio import limits
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_stars_are_capped_and_deduplicated(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.breed_tiles = [{"id": f"tile{i}"} for i in range(100)]
        for i in range(100):
            r = client.post("/fragment/breed/star", data={"id": f"tile{i}"})
            assert r.status_code == 200
        assert len(s.breed_starred) <= limits.MAX_STARRED
        assert len(s.breed_starred) == len(set(s.breed_starred))  # deduplicated
        # keeps the *last* MAX_STARRED ids, not the first
        assert s.breed_starred[-1] == "tile99"


def test_star_rejects_ids_not_in_breed_tiles(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.breed_tiles = [{"id": "real"}]
        client.post("/fragment/breed/star", data={"id": "made-up"})
        assert "made-up" not in s.breed_starred


def test_scenes_are_capped(tmp_path):
    # v3: a captured scene is a song section, capped at MAX_SECTIONS
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        for _ in range(limits.MAX_SECTIONS + 4):
            client.post("/song/from-loop", data={"name": "x"})
        assert len(s.song["sections"]) <= limits.MAX_SECTIONS


def test_scene_capture_past_the_cap_is_a_422_toast(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        for _ in range(limits.MAX_SECTIONS):
            r = client.post("/song/from-loop", data={"name": "x"})
            assert r.status_code == 200
        r = client.post("/song/from-loop", data={"name": "x"})
        assert r.status_code == 422 and "toast" in r.text
        assert len(s.song["sections"]) == limits.MAX_SECTIONS


# --- named grid/tempo limits: one source, not a literal in every
# route, arrange.py clamp and template that needs it -------------------------


def test_bpm_range_matches_the_clamps_everywhere_it_used_to_be_a_literal():
    assert limits.BPM_RANGE == (40.0, 200.0)


def test_bar_choices():
    assert limits.BAR_CHOICES == (1, 2, 3, 4)


def test_a_pool_pattern_may_be_eight_bars_and_the_loop_may_not():
    """`PATTERN_BAR_CHOICES` adds 8 for pool patterns while bars x steps
    fits the grid; the Perform loop keeps `BAR_CHOICES` (its render is one
    piece). `MAX_GRID_STEPS` did not move."""
    from synthlab.studio import song_model as sm

    assert limits.PATTERN_BAR_CHOICES == (1, 2, 3, 4, 8)
    assert limits.MAX_GRID_STEPS == 128
    assert sm.bar_choices(16) == (1, 2, 3, 4, 8)
    assert sm.bar_choices(32) == (1, 2, 3, 4)
    assert sm.bar_choices(4) == (1, 2, 3, 4, 8)


def test_the_song_creation_limits():
    """The roots, tempo and lengths a song may have."""
    from synthlab.studio.state import ROOTS, StudioState

    assert (limits.MAX_TRACKS, limits.MAX_SYNTH_TRACKS) == (12, 8)
    assert (limits.MAX_AUDIO_TRACKS, limits.MAX_DRUM_TRACKS) == (3, 1)
    assert limits.MAX_TRACKS == (
        limits.MAX_SYNTH_TRACKS + limits.MAX_AUDIO_TRACKS + limits.MAX_DRUM_TRACKS
    )
    assert limits.MAX_PLAYLIST_TILES == 6000
    assert (limits.MAX_RETURNS, limits.MAX_CHORD_SLOTS) == (2, 64)
    assert (limits.MAX_JAM_EVENTS, limits.MAX_RANGE_CELLS) == (512, 96)
    assert limits.MAX_DRUM_HITS == limits.MAX_DRUM_CELLS
    assert StudioState.SONG_UNDO_BYTES == 8 * 1024 * 1024
    # every key: each semitone from A0 to E3, the six old roots among them
    assert len(ROOTS) == 32 and ROOTS[0] == "A0" and ROOTS[-1] == "E3"
    assert {"A0", "E1", "A1", "E2", "A2", "E3", "F#1", "C2"} <= set(ROOTS)
    # unchanged, on purpose
    assert limits.MAX_SECTION_SECONDS == 120.0 and limits.LAYER_SLOTS == 4
    assert (limits.MAX_SECTIONS, limits.MAX_CELL_LANES) == (32, 4)


def test_step_choices_and_drum_step_choices_are_named_separately():
    """Same values today, but named separately on purpose: the
    synth grid and the drum machine's own clock must never be conflated by
    sharing one name that later drifts for only one of them."""
    assert limits.STEP_CHOICES == (4, 8, 16, 32)
    assert limits.DRUM_STEP_CHOICES == (4, 8, 16, 32)


def test_arrangement_from_form_uses_bpm_range(tmp_path):
    from synthlab.studio.arrange import arrangement_from_form

    arr = arrangement_from_form({"bpm": "9999"})
    assert arr.bpm == limits.BPM_RANGE[1]
    arr = arrangement_from_form({"bpm": "1"})
    assert arr.bpm == limits.BPM_RANGE[0]


def test_max_song_seconds_lives_in_limits(tmp_path):
    """Minor #5: MAX_SONG_SECONDS used to be a module-level literal in
    routes/export.py, the one hard bound in that module not named here
    alongside every other one."""
    import synthlab.studio.routes.export as export_mod

    # 240 until Oct 2026; 480 once the loudness master worked in float32
    # (measured: 1.26 GB of peak RSS at 480 s with 8 synths, under the
    # 2.0 GB the cap was conditional on — tools/song_measure.py)
    assert limits.MAX_SONG_SECONDS == 480.0
    assert not hasattr(export_mod, "MAX_SONG_SECONDS")
