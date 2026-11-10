"""State v2 -> v3: the legacy scenes + running order become the song model.

The fixtures under tests/fixtures/legacy_states/ are real `state.json`
payloads written by the pre-song studio (see build_legacy_states.py there):
scenes from `capture_scene`, a modified drum bank, layers and clip lanes.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import schema, song_bridge
from synthlab.studio import song_model as sm
from synthlab.studio.render import RenderInputs
from synthlab.studio.server import create_app
from synthlab.studio.song_bridge import section_render_inputs
from synthlab.studio.state import STATE_VERSION, Arrangement, StudioState
from synthlab.synth import Patch

N = schema.nodes()
FIXTURES = Path(__file__).parents[1] / "fixtures" / "legacy_states"


def _legacy(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _load(tmp_path: Path, data: dict) -> StudioState:
    (tmp_path / "state.json").write_text(json.dumps(data), encoding="utf-8")
    return StudioState(tmp_path)


def _counts(song: dict) -> dict:
    kinds = [t["kind"] for t in song["tracks"]]
    pool = song["patterns"].values()
    return {
        "sections": len(song["sections"]),
        "drum_patterns": sum(p["kind"] == "drums" for p in pool),
        "note_patterns": sum(p["kind"] == "notes" for p in pool),
        "drums": kinds.count("drums"),
        "synth": kinds.count("synth"),
        "audio": kinds.count("audio"),
        "takes": sum(len(s["audio"]) for s in song["sections"]),
    }


def _legacy_inputs(sc: dict, clip_root) -> RenderInputs:
    """The old scene render (routes/export.py `_scene_render_args` at v2),
    over the scene as `_apply` conformed it."""
    sc = schema.conform(sc, N.SCENE)
    return RenderInputs(
        patch=Patch.from_dict(sc["patch"]),
        arr=Arrangement(**sc["arr"]),
        curves=sc["curves"],
        layers=sc["layers"],
        main_muted=sc["main_muted"],
        main_fx=sc["main_fx"],
        master_fx=sc["master_fx"],  # v2 rendered each scene with its own
        drums_fx=sc["fx_drums"],
        parts=("synth", "drums", "clips"),
        clip_root=clip_root,
    )


def _section_of_scene(legacy: dict, song: dict, i: int) -> dict:
    """The section scene `i` became: running-order entries first, then the
    scenes the order left out."""
    order = [e["scene"] for e in legacy["song"]]
    order += [j for j in range(len(legacy["scenes"])) if j not in order]
    return song["sections"][order.index(i)]


# --- each fixture migrates -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "with_scenes",
            # 8 bank slots (every scene's beat is one of them); notes: intro
            # main, the shared Bright riff (intro layer = chorus/bridge
            # main), verse main, verse layer (2-bar grid), the Dark riff
            # (chorus/bridge layer). Tracks: Synth 2 (active, Brighter),
            # Synth 1 (Dark), Bright, and a copy of Dark (the chorus plays
            # Bright over Dark — the reverse of the intro's order).
            {
                "sections": 4,
                "drum_patterns": 8,
                "note_patterns": 5,
                "drums": 1,
                "synth": 4,
                "audio": 0,
                "takes": 0,
            },
        ),
        (
            "no_scenes",
            # Loop 1: its beat is "clappy" plus a velocity lane the bank
            # doesn't hold, so its own; the main grid and layer 0's notes.
            {
                "sections": 1,
                "drum_patterns": 9,
                "note_patterns": 2,
                "drums": 1,
                "synth": 3,
                "audio": 0,
                "takes": 0,
            },
        ),
        (
            "with_clips",
            {
                "sections": 2,
                "drum_patterns": 9,
                "note_patterns": 1,
                "drums": 1,
                "synth": 1,
                "audio": 1,
                "takes": 3,
            },
        ),
    ],
)
def test_each_fixture_migrates(tmp_path, name, expected):
    legacy = _legacy(name)
    assert legacy["v"] == 2
    s = _load(tmp_path, legacy)
    assert _counts(s.song) == expected
    assert schema.conform_strict(s.song, N.SONG) == s.song
    assert not hasattr(s, "scenes")
    # the working loop is untouched by the migration
    assert s.arrangement == Arrangement(
        **schema.conform(legacy["arrangement"], N.ARRANGEMENT)
    )
    assert s.patch.to_json() == schema.conform(legacy["patch"], N.PATCH)
    payload = s._payload()
    assert payload["v"] == STATE_VERSION == 3
    assert "scenes" not in payload
    assert isinstance(payload["song"], dict)


def test_scenes_become_sections_in_running_order(tmp_path):
    legacy = _legacy("with_scenes")
    song = _load(tmp_path, legacy).song
    names = [(s["name"], s["repeats"], s["bars"]) for s in song["sections"]]
    # the running order with its repeats; "bridge" was left out of it, so
    # it comes last rather than being lost
    assert names == [
        ("intro", 1, 1),
        ("verse", 2, 2),
        ("chorus", 1, 1),
        ("bridge", 1, 1),
    ]
    drums = next(t["id"] for t in song["tracks"] if t["kind"] == "drums")
    beats = [s["cells"][drums] for s in song["sections"]]
    pool = song["patterns"]
    # every scene's beat was a bank slot's: shared, under the slot's name
    assert [pool[b]["name"] for b in beats] == ["House", "Techno", "House", "mine"]
    assert beats[0] == beats[2]
    # the bank's drum_bars/drum_steps clock became the pattern's bars/steps
    assert all(pool[b]["bars"] == 1 and pool[b]["steps"] == 16 for b in beats)


def test_a_scene_listed_twice_is_two_sections(tmp_path):
    legacy = _legacy("with_scenes")
    legacy["song"] = [{"scene": 1, "repeats": 1}, {"scene": 1, "repeats": 3}]
    song = _load(tmp_path, legacy).song
    names = [(s["name"], s["repeats"]) for s in song["sections"]]
    assert names[:2] == [("verse", 1), ("verse", 3)]
    assert song["sections"][0]["cells"] == song["sections"][1]["cells"]
    assert {n for n, _r in names} == {"intro", "verse", "chorus", "bridge"}


def test_bank_slots_become_named_drum_patterns(tmp_path):
    legacy = _legacy("no_scenes")
    s = _load(tmp_path, legacy)
    names = {p["name"] for p in s.song["patterns"].values() if p["kind"] == "drums"}
    assert {p["name"] for p in legacy["drum_bank"] if p} <= names
    # the bank itself still drives the Drums view's bank UI
    assert s.drum_bank == schema.conform(
        legacy["drum_bank"], N.PAYLOAD.fields["drum_bank"]
    )
    # the active slot's pattern is in the pool, found by content (there is
    # no StudioState.slot_pattern_id; Perform's launcher reads the pool)
    slot = s.drum_bank[legacy["active_slot"]]
    probe = schema.conform(
        {**song_bridge.bank_data(slot), "id": "probe", "kind": "drums"},
        N.SONG_PATTERN,
    )
    pid = song_bridge.pattern_index(s.song)[sm.content_hash(probe)]
    assert s.song["patterns"][pid]["name"] == "clappy"


def test_no_scenes_makes_the_working_loop_one_section(tmp_path):
    legacy = _legacy("no_scenes")
    song = _load(tmp_path, legacy).song
    (sec,) = song["sections"]
    assert sec["name"] == "Loop 1"
    synth = [t for t in song["tracks"] if t["kind"] == "synth"]
    # the active slot's track first (it is the loop's main lane), each
    # named for its layer slot; the patch-only slot 3 is kept as a track
    assert [t["name"] for t in synth] == ["Synth 2", "Synth 1", "Synth 3"]
    assert [t["patch"]["name"] for t in synth] == ["Lead", "init", "Unused"]
    cells = [song["patterns"][sec["cells"][t["id"]]]["cells"] for t in synth[:2]]
    assert cells == [[["C2", 1], ["D2", 2]], [["A1", 0], ["E2", 3]]]
    assert sec["cells"].get(synth[2]["id"]) is None


def test_clip_lanes_become_takes_on_one_audio_track(tmp_path):
    legacy = _legacy("with_clips")
    s = _load(tmp_path, legacy)
    audio = next(t for t in s.song["tracks"] if t["kind"] == "audio")
    takes = [
        [(a["clip"], a["at_bar"], a["gain"]) for a in sec["audio"]]
        for sec in s.song["sections"]
    ]
    assert takes == [[("riser", 1, 0.8)], [("vox", 0, 1.0), ("riser", 1, 1.2)]]
    assert all(
        a["track_id"] == audio["id"] for sec in s.song["sections"] for a in sec["audio"]
    )
    drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    assert drums["drum_samples"] == {"snare": "vox"}
    beat = s.song["patterns"][s.song["sections"][0]["cells"][drums["id"]]]
    assert (beat["bars"], beat["steps"]) == (2, 16)  # the renamed drum clock


@pytest.mark.parametrize("name", ["with_scenes", "with_clips"])
def test_every_migrated_scene_renders_what_it_rendered(tmp_path, name):
    """The migration is lossless where it matters: each section's loop is
    the scene's own render input, key for key (patch, grid, layers, kit,
    FX, clip lane)."""
    legacy = _legacy(name)
    s = _load(tmp_path, legacy)
    for i, sc in enumerate(legacy["scenes"]):
        sec = _section_of_scene(legacy, s.song, i)
        old = _legacy_inputs(sc, tmp_path)
        new = section_render_inputs(
            s.song, sec, master_fx=s.fx_master, clip_root=tmp_path
        )
        assert new.key() == old.key(), sc["name"]


@pytest.mark.parametrize("name", ["with_scenes", "with_clips"])
def test_opening_a_migrated_section_puts_the_scene_in_the_working_loop(tmp_path, name):
    legacy = _legacy(name)
    with TestClient(create_app(data_dir=_seeded(tmp_path, legacy))) as client:
        svc = client.app.state.svc
        s: StudioState = svc.state
        for i, sc in enumerate(legacy["scenes"]):
            assert s.section_to_loop(_section_of_scene(legacy, s.song, i)["id"])
            live = replace(
                svc.render_inputs("perform"), parts=("synth", "drums", "clips")
            )
            assert live.key() == _legacy_inputs(sc, tmp_path).key()


def _parts_inputs(parts, master_fx, clip_root) -> RenderInputs:
    return parts.inputs(master_fx=master_fx, clip_root=clip_root)


@pytest.mark.parametrize(
    ("name", "differs"),
    [("with_scenes", set()), ("with_clips", set()), ("divergent", {"c"})],
)
def test_section_arrangement_renders_each_legacy_scene(tmp_path, name, differs):
    """Every migrated scene's SectionParts render the scene's own inputs,
    key for key, except where the song had to give a scene's own tempo /
    kit / master FX up (`divergent.json`'s scene c — the known loss)."""
    legacy = _legacy(name)
    s = _load(tmp_path, legacy)
    for i, sc in enumerate(legacy["scenes"]):
        parts = sm.section_arrangement(s.song, _section_of_scene(legacy, s.song, i))
        assert not parts.flat, sc["name"]  # the scene's own patterns, as they are
        new = _parts_inputs(parts, s.fx_master, tmp_path)
        same = new.key() == _legacy_inputs(sc, tmp_path).key()
        assert same is (sc["name"] not in differs), sc["name"]


def test_section_arrangement_of_loop_1_is_the_working_loop(tmp_path):
    """No scenes: the working loop became "Loop 1", and its parts render
    what the loop renders (Perform's inputs, clips included)."""
    s = _load(tmp_path, _legacy("no_scenes"))
    parts = sm.section_arrangement(s.song, s.song["sections"][0])
    loop = RenderInputs(
        patch=s.patch,
        arr=s.arrangement,
        curves=s.curves,
        layers=s.other_layers(),
        main_muted=bool(s.layers[s.active_layer].get("muted")),
        main_fx=s.layers[s.active_layer].get("fx") or [],
        master_fx=s.fx_master,
        drums_fx=s.fx_drums,
        parts=("synth", "drums", "clips"),
        clip_root=tmp_path,
    )
    assert _parts_inputs(parts, s.fx_master, tmp_path).key() == loop.key()


def _seeded(tmp_path: Path, data: dict) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(data), encoding="utf-8")
    return d


# --- persistence ---------------------------------------------------------------


def test_a_v3_state_reloads_byte_identically(tmp_path):
    s = _load(tmp_path, _legacy("with_scenes"))
    s.save()
    first = (tmp_path / "state.json").read_bytes()
    StudioState(tmp_path).save()
    assert (tmp_path / "state.json").read_bytes() == first
    assert json.loads(first)["v"] == 3


def test_migrating_keeps_a_copy_of_the_v2_file(tmp_path):
    legacy = _legacy("with_scenes")
    _load(tmp_path, legacy).save()
    backup = tmp_path / "state.json.v2.bak"
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy
    # a second start (now v3) leaves the backup alone
    StudioState(tmp_path).save()
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy


def test_project_export_import_round_trips_the_song(tmp_path):
    src = _seeded(tmp_path, _legacy("with_scenes"))
    with TestClient(create_app(data_dir=src)) as a:
        proj = a.get("/project.json").json()
        song = a.app.state.studio.song
    assert proj["v"] == 3 and proj["song"] == song and "scenes" not in proj
    dst = tmp_path / "other"
    with TestClient(create_app(data_dir=dst)) as b:
        r = b.post(
            "/project/import",
            files={"file": ("p.json", json.dumps(proj).encode())},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert b.app.state.studio.song == song
        assert b.get("/project.json").json() == proj


def test_an_old_project_migrates_on_import(tmp_path):
    legacy = _legacy("with_clips")
    with TestClient(create_app(data_dir=tmp_path)) as c:
        r = c.post(
            "/project/import",
            files={
                "file": (
                    "p.json",
                    json.dumps({"format": "synthlab-project", **legacy}).encode(),
                )
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        song = c.app.state.studio.song
        assert [s["name"] for s in song["sections"]] == ["build", "drop"]


@pytest.mark.parametrize(
    "junk",
    [None, 5, "x", [], {}, [None, 5, "x"], [{"arr": 5}], [{"patch": [1], "layers": 7}]],
)
def test_migration_never_raises_on_hostile_scenes(tmp_path, junk):
    legacy = _legacy("with_scenes")
    legacy["scenes"] = junk
    legacy["song"] = [{"scene": 0}, "junk", {"scene": 1e9}]
    s = _load(tmp_path, legacy)
    assert schema.conform_strict(s.song, N.SONG) == s.song
    assert not list(tmp_path.glob("state.json.corrupt-*"))


def test_migration_of_a_bare_v1_file(tmp_path):
    s = _load(
        tmp_path,
        {"patch": None, "arrangement": {"bpm": 133.0}, "slot_a": None, "slot_b": None},
    )
    assert s.arrangement.bpm == 133.0
    assert s.song["bpm"] == 133.0
    assert [x["name"] for x in s.song["sections"]] == ["Loop 1"]


def test_too_many_scenes_stop_at_the_section_cap(tmp_path):
    from synthlab.studio import limits

    legacy = _legacy("with_scenes")
    legacy["song"] = [{"scene": i % 4, "repeats": 1} for i in range(64)]
    s = _load(tmp_path, legacy)
    assert len(s.song["sections"]) == limits.MAX_SECTIONS


# --- the bridges ---------------------------------------------------------------


def test_loop_to_section_dedupes_patterns_and_reuses_tracks(tmp_path):
    s = StudioState(tmp_path)
    a = s.loop_to_section("A")
    b = s.loop_to_section("B")
    assert a != b
    first, second = s.song["sections"]
    assert first["cells"] == second["cells"]  # identical loops share patterns
    assert [t["kind"] for t in s.song["tracks"]] == ["drums", "synth"]
    s.patch = replace(s.patch, cutoff=321.0)
    s.loop_to_section("C")
    synth = [t for t in s.song["tracks"] if t["kind"] == "synth"]
    assert len(synth) == 2 and synth[1]["patch"]["cutoff"] == 321.0
    # the note pattern is shared across the sound change
    assert (
        s.song["sections"][2]["cells"][synth[1]["id"]] == first["cells"][synth[0]["id"]]
    )


def test_loop_to_section_is_atomic_on_a_cap(tmp_path, monkeypatch):
    from synthlab.studio import limits

    s = StudioState(tmp_path)
    monkeypatch.setattr(limits, "MAX_SECTIONS", 1)
    s.loop_to_section("A")
    before = copy.deepcopy(s.song)
    with pytest.raises(sm.SongError):
        s.loop_to_section("B")
    assert s.song == before


def test_section_to_loop_round_trips_the_working_loop(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        svc = client.app.state.svc
        s: StudioState = svc.state
        s.arrangement = replace(
            s.arrangement,
            bars=2,
            steps_per_bar=8,
            cells=[["A1", 0], ["C2", 9]],
            velocities={"9": 0.5},
            drums=[["kick", 0], ["hat", 4]],
            drum_bars=2,
        )
        s.select_layer(1)
        s.patch = replace(s.patch, cutoff=2000.0)
        s.arrangement = replace(s.arrangement, cells=[["E2", 3]])
        s.select_layer(0)
        s.curves = {"cutoff": [[0.0, 0.5], [1.0, 1.0]]}
        want = replace(svc.render_inputs("perform"), parts=("synth", "drums", "clips"))
        sid = s.loop_to_section("verse")
        s.arrangement = Arrangement()
        s.patch = Patch()
        assert s.section_to_loop(sid)
        got = replace(svc.render_inputs("perform"), parts=("synth", "drums", "clips"))
        assert got.key() == want.key()
        assert s.active_slot is None
        assert s.baseline_label("sequence") == "verse"
        assert not s.section_to_loop("nope")


def test_a_loop_section_loop_round_trip_keeps_each_layers_sequencing(tmp_path):
    """Each layer's own velocities, slides and arp go into its pattern
    (`layer_note_data`) and come back into its slot's `seq` (`to_loop`),
    and the loop renders what it rendered. Layer 2 has no velocity lane
    of its own under a main lane that has one: its pattern spells its
    0.9s out, or the song would play the main lane's there."""
    with TestClient(create_app(data_dir=tmp_path)) as client:
        svc = client.app.state.svc
        s: StudioState = svc.state
        s.arrangement = replace(
            s.arrangement,
            steps_per_bar=8,
            cells=[["A1", 0], ["C2", 3]],
            velocities={"0": 0.5, "3": 0.2},
        )
        s.select_layer(1)
        s.patch = replace(s.patch, cutoff=2000.0)
        s.arrangement = replace(
            s.arrangement,
            cells=[["E2", 1], ["G2", 4]],
            velocities={"1": 1.0, "6": 0.3},
            slides=[],
            arp_mode="up",
            arp_octaves=2,
        )
        s.select_layer(2)
        s.patch = replace(s.patch, cutoff=700.0)
        s.arrangement = replace(
            s.arrangement,
            cells=[["A2", 0], ["C3", 2], ["E3", 3]],
            velocities={},
            slides=[2, 3],
            arp_mode="off",
            arp_octaves=1,
        )
        s.select_layer(0)
        seqs = [s.layers[i]["seq"] for i in (1, 2)]
        want = replace(svc.render_inputs("perform"), parts=("synth", "drums", "clips"))
        assert [
            set(lay) - {"patch", "cells", "muted", "gain", "fx"} for lay in want.layers
        ] == [
            {"velocities", "arp_mode", "arp_octaves"},
            {"velocities", "slides"},
        ]
        sid = s.loop_to_section("verse")
        sec = s.song["sections"][sm.section_index(s.song, sid)]
        parts = sm.section_arrangement(s.song, sec)
        assert not parts.flat and parts.layers == want.layers
        s.arrangement = Arrangement()
        s.patch = Patch()
        assert s.section_to_loop(sid)
        assert s.layers[1]["seq"] == seqs[0]
        assert s.layers[2]["seq"]["slides"] == seqs[1]["slides"]
        assert s.layers[2]["seq"]["velocities"] == {"0": 0.9, "2": 0.9, "3": 0.9}
        got = replace(svc.render_inputs("perform"), parts=("synth", "drums", "clips"))
        assert got.key() == want.key()


# --- editing a track's sound on Synths -----------------------------------------


SYNTHS = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
PERFORM = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}


def _editing(client: TestClient, cutoff: float = 333.0) -> tuple[StudioState, dict]:
    """A song with one synth track at `cutoff`, a working loop at 4444 with
    its own undo history and slot_prev, and the track's sound being edited."""
    s: StudioState = client.app.state.studio
    s.patch = replace(s.patch, cutoff=cutoff)
    s.loop_to_section("A")
    s.patch = replace(s.patch, cutoff=4444.0)
    s.push_history()  # the loop's own undo step (back to 4444)
    s.slot_prev = replace(s.patch, cutoff=55.0)  # the loop's "before last load"
    tid = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
    assert s.edit_track(tid)
    assert s.patch.cutoff == cutoff
    return s, s.song["tracks"][sm.track_index(s.song, tid)]


def test_patch_edits_on_synths_mirror_into_the_track(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s: StudioState = client.app.state.studio
        s.loop_to_section("A")
        tid = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
        track = s.song["tracks"][sm.track_index(s.song, tid)]
        # nothing mirrors until a track is being edited
        client.post("/fragment/patch", data={"cutoff": "900"}, headers=SYNTHS)
        assert track["patch"]["cutoff"] != 900.0
        assert s.edit_track(tid)
        assert s.song_edit_track == tid
        client.post("/fragment/patch", data={"cutoff": "1234"}, headers=SYNTHS)
        assert track["patch"]["cutoff"] == 1234.0
        # any route that changes the patch, not just the slider post
        client.post("/fragment/ab/copy_a", headers=SYNTHS)
        s.slot_b = replace(s.patch, cutoff=777.0)
        client.post("/fragment/ab/swap", headers=SYNTHS)  # ⇄: to B
        assert track["patch"]["cutoff"] == 777.0
        # a request that says nowhere where it came from never mirrors
        client.post("/fragment/patch", data={"cutoff": "600"})
        assert track["patch"]["cutoff"] == 777.0
        # runtime only: never persisted, and the session file keeps the
        # loop's own patch, not the track's
        assert "song_edit_track" not in s._payload()
        assert s._payload()["patch"]["cutoff"] != 600.0
        s.save()
        assert StudioState(tmp_path).song_edit_track is None
        # opening a section into the loop ends the edit
        assert s.section_to_loop(s.song["sections"][0]["id"])
        assert s.song_edit_track is None


def test_restore_prev_and_undo_never_reach_the_loops_sound(tmp_path):
    """The repro: edit_track, then restore_prev wrote the loop's
    4444 into the track. The edit has its own (empty) slot_prev and undo."""
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s, track = _editing(client)
        client.post("/fragment/restore_prev", headers=SYNTHS)
        assert track["patch"]["cutoff"] == 333.0 and s.patch.cutoff == 333.0
        client.post("/fragment/undo", headers=SYNTHS)
        assert track["patch"]["cutoff"] == 333.0 and s.patch.cutoff == 333.0
        # the edit's own undo still works, inside the edit
        client.post("/fragment/patch", data={"cutoff": "999"}, headers=SYNTHS)
        assert track["patch"]["cutoff"] == 999.0
        client.post("/fragment/undo", headers=SYNTHS)
        assert track["patch"]["cutoff"] == 333.0


def test_select_layer_ends_the_edit_and_restores_the_loop(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s, track = _editing(client)
        r = client.post("/fragment/layer/select", data={"index": "1"}, headers=SYNTHS)
        assert r.status_code == 200
        assert s.song_edit_track is None
        assert track["patch"]["cutoff"] == 333.0
        # layer 0 was stashed with the loop's patch, not the track's
        assert s.layers[0]["patch"]["cutoff"] == 4444.0


@pytest.mark.parametrize(
    ("method", "path", "data"),
    [
        # a Perform lane head's sound ▾
        (
            "post",
            "/fragment/perform/lane/sound",
            {"index": "0", "sound": "preset:Lead"},
        ),
        ("post", "/fragment/patch", {"cutoff": "2222"}),  # a second tab on Perform
        ("get", "/view/perform", None),  # navigating away
    ],
)
def test_a_request_from_another_view_ends_the_edit(tmp_path, method, path, data):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s, track = _editing(client)
        (tmp_path / "presets").mkdir(exist_ok=True)
        (tmp_path / "presets" / "Lead.json").write_text(
            json.dumps(replace(s.patch, cutoff=1500.0, name="Lead").to_json())
        )
        kwargs = {"headers": PERFORM}
        if data is not None:
            kwargs["data"] = data
        getattr(client, method)(path, **kwargs)
        assert s.song_edit_track is None
        assert track["patch"]["cutoff"] == 333.0


def test_ending_the_edit_restores_the_loop(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s, track = _editing(client)
        client.post("/fragment/patch", data={"cutoff": "999"}, headers=SYNTHS)
        assert s.end_track_edit()
        assert track["patch"]["cutoff"] == 999.0  # the edit landed in the track
        assert s.patch.cutoff == 4444.0  # and the loop has its own sound back
        assert s.slot_prev.cutoff == 55.0
        assert s.pop_history() and s.patch.cutoff == 4444.0  # its own undo ring
        assert not s.end_track_edit()


def test_deleting_the_edited_track_ends_the_edit(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s, track = _editing(client)
        s.song["sections"].clear()
        assert sm.remove_track(s.song, track["id"]) == []
        client.post("/fragment/patch", data={"cutoff": "999"}, headers=SYNTHS)
        assert s.song_edit_track is None
        assert s.patch.cutoff == 4444.0


def test_edit_track_refuses_a_track_with_no_sound(tmp_path):
    """A synth track's sound and the drums track's kit can be edited (the
    kit from the Drum Lab: `_edit_kit`); an audio track holds takes, and a
    track that is not there holds nothing."""
    s = StudioState(tmp_path)
    s.loop_to_section("A")
    audio = sm.add_track(s.song, "audio", "Vox")
    assert not s.edit_track(audio) and s.song_edit_track is None
    assert not s.edit_track("missing")
    drums = next(t["id"] for t in s.song["tracks"] if t["kind"] == "drums")
    kit = {k: getattr(s.arrangement, k) for k in ("drum_params", "drum_mix")}
    assert s.edit_track(drums) and s.track_edit_view() == "drums"
    assert s.end_track_edit() and s.track_edit_view() is None
    assert {k: getattr(s.arrangement, k) for k in kit} == kit


# --- the rest of the state still reaches the song ------------------------------


def test_a_clip_rename_follows_into_the_song(tmp_path):
    s = _load(tmp_path, _legacy("with_clips"))
    s.rename_clip_refs("vox", "voice")
    clips = [a["clip"] for sec in s.song["sections"] for a in sec["audio"]]
    assert clips == ["riser", "voice", "riser"]
    drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    assert drums["drum_samples"] == {"snare": "voice"}


def test_the_song_is_a_reset_scope_owned_by_the_song_view(tmp_path):
    from synthlab.studio.state import RESET_SCOPES, RESET_VIEWS
    from synthlab.studio.views import VIEWS

    assert RESET_SCOPES["song"][1] == ("state.song",)
    assert VIEWS["song"].parts == ("synth", "drums", "clips")
    assert not VIEWS["song"].bench and RESET_VIEWS["song"] == ("song",)
    s = _load(tmp_path, _legacy("with_scenes"))
    migrated = copy.deepcopy(s.song)
    assert s.baseline_label("song") == "as opened"
    s.song["sections"].pop()
    assert s.is_dirty("song")
    assert s.revert("song") and s.song == migrated
    assert s.reset_defaults("song") and s.song["sections"] == []


@pytest.mark.parametrize("v", [None, 2, "3", 3.5, True])
def test_a_song_dict_is_never_migrated_away(tmp_path, v):
    """The shape of `song` decides, not "v": a damaged "v" next to a real
    song must not rebuild the song from the working loop."""
    s = _load(tmp_path, _legacy("with_scenes"))
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    song = data["song"]
    data["v"] = v
    other = tmp_path / "again"
    other.mkdir()
    assert _load(other, data).song == song


# --- scenes that disagree with the session and with each other -------------------


def test_divergent_scenes_adopt_the_first_scenes_values(tmp_path, caplog):
    """A song has one tempo, key, groove, kit and master chain. The first
    scene in running order supplies them (not the session, which had moved
    on to 90 bpm and another kit); the master FX stay the session's. A
    scene that disagrees is logged and named in the one-time note — and
    renders differently, which the test says out loud rather than letting
    a key equality pass by accident."""
    legacy = _legacy("divergent")
    a, b, c = (schema.conform(sc, N.SCENE) for sc in legacy["scenes"])
    assert legacy["arrangement"]["bpm"] == 90.0 and a["arr"]["bpm"] == 128.0
    with caplog.at_level("WARNING", logger="synthlab.studio.song_bridge"):
        s = _load(tmp_path, legacy)
    song = s.song
    assert (song["bpm"], song["root"], song["swing"]) == (128.0, "A1", 0.1)
    drums = next(t for t in song["tracks"] if t["kind"] == "drums")
    assert drums["drum_params"] == a["arr"]["drum_params"]
    assert drums["drum_mix"] == a["arr"]["drum_mix"]
    assert drums["fx"] == a["fx_drums"]
    # the working loop keeps its own 90 bpm and kit
    assert s.arrangement.bpm == 90.0
    assert s.arrangement.drum_params == {"snare": {"decay": 0.3}}
    assert s.fx_master == schema.conform(legacy["fx_master"], N.FX)

    for i, sc in enumerate((a, b)):  # agree with the song: exactly the old render
        sec = _section_of_scene(legacy, song, i)
        new = section_render_inputs(
            song, sec, master_fx=s.fx_master, clip_root=tmp_path
        )
        assert new.key() == _legacy_inputs(sc, tmp_path).key(), sc["name"]
    sec_c = _section_of_scene(legacy, song, 2)
    new_c = section_render_inputs(
        song, sec_c, master_fx=s.fx_master, clip_root=tmp_path
    )
    assert new_c.key() != _legacy_inputs(c, tmp_path).key()  # the known loss
    assert new_c.arr.bpm == 128.0 and new_c.arr.root == "A1"
    assert new_c.drums_fx == tuple(a["fx_drums"])
    # c's own notes and beat are kept
    assert new_c.arr.cells == c["arr"]["cells"] and new_c.arr.drums == c["arr"]["drums"]

    warned = [r.getMessage() for r in caplog.records]
    assert warned == [
        "migration: scene 'c' now plays with the song's "
        "tempo 100 -> 128 bpm, key, kit, master FX"
    ]
    note = song["migration_note"]
    assert "c (tempo 100 -> 128 bpm, key, kit, master FX)" in note
    assert "state.json.v2.bak" in note
    assert "a (" not in note and "b (" not in note  # only the scene that lost


def test_agreeing_scenes_leave_no_migration_note(tmp_path):
    for name in ("with_scenes", "with_clips", "no_scenes"):
        (tmp_path / name).mkdir()
        assert _load(tmp_path / name, _legacy(name)).song["migration_note"] == ""


def test_the_migration_note_is_capped(tmp_path):
    from synthlab.studio import limits

    legacy = _legacy("divergent")
    c = legacy["scenes"][2]
    legacy["scenes"] = [legacy["scenes"][0]] + [
        {**c, "name": f"{'x' * 30}{i}"} for i in range(12)
    ]
    legacy["song"] = []
    note = _load(tmp_path, legacy).song["migration_note"]
    assert "and 7 more" in note
    assert len(note) <= limits.MAX_MIGRATION_NOTE_CHARS


# --- a sound with no synth track left (review: a full song reused track 1) --------


def _five_sounds(s: StudioState) -> None:
    from synthlab.studio import limits

    for i in range(limits.MAX_SYNTH_TRACKS):
        s.patch = replace(s.patch, cutoff=300.0 + 50 * i)
        s.loop_to_section(f"L{i}")


def test_loop_to_section_refuses_a_sound_with_no_track_left(tmp_path):
    """Five synth tracks, a sixth sound: no track can be added. The section
    used to land on track 1, playing track 1's patch, without a word."""
    s = StudioState(tmp_path)
    _five_sounds(s)
    before = copy.deepcopy(s.song)
    s.patch = replace(s.patch, cutoff=999.0)
    with pytest.raises(sm.SongError, match="synth track"):
        s.loop_to_section("sixth")
    assert s.song == before
    # a sound a track already has still goes in
    s.patch = replace(s.patch, cutoff=300.0)
    s.loop_to_section("first again")
    assert len(s.song["sections"]) == len(before["sections"]) + 1


def test_add_loop_to_a_full_song_is_a_toast(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        s: StudioState = client.app.state.studio
        _five_sounds(s)
        before = copy.deepcopy(s.song)
        s.patch = replace(s.patch, cutoff=999.0)
        r = client.post("/song/from-loop", data={"name": "sixth"})
        assert r.status_code == 422 and "synth track" in r.text
        # the toast sizes the fix: how many of the loop's sounds are left over
        assert "1 of this loop&#39;s 1 sound(s)" in r.text
        assert s.song == before


def test_scenes_past_the_synth_track_cap_are_named_in_the_note(tmp_path, caplog):
    """Eight scenes, eight sounds, five synth tracks: the migration keeps
    every scene, but the last ones play a track that exists. They are named
    in the one-time note (it used to be empty) and logged."""
    legacy = _legacy("with_scenes")
    sc = legacy["scenes"][0]
    legacy["scenes"] = [
        {
            **sc,
            "name": f"sc{i}",
            "layers": [],
            "patch": {**sc["patch"], "cutoff": 300.0 + 50 * i},
        }
        for i in range(8)
    ]
    legacy["song"] = []
    with caplog.at_level("WARNING", logger="synthlab.studio.song_bridge"):
        song = _load(tmp_path, legacy).song
    assert len(song["sections"]) == 8  # no scene is lost
    synth = [t for t in song["tracks"] if t["kind"] == "synth"]
    crowded = [
        s["name"]
        for s, old in zip(song["sections"], legacy["scenes"], strict=True)
        if not any(
            s["cells"].get(t["id"]) and t["patch"]["cutoff"] == old["patch"]["cutoff"]
            for t in synth
        )
    ]
    assert crowded and crowded[-1] == "sc7"
    note = song["migration_note"]
    assert "synth tracks" in note and "state.json.v2.bak" in note
    tail = note.split("another scene's track: ")[1]
    assert tail.startswith("; ".join(crowded[:5]))
    assert "sc0" not in tail
    assert any("sc7" in r.getMessage() for r in caplog.records)
