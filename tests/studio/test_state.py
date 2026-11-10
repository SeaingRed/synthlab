import json
from dataclasses import fields

from synthlab.studio.state import (
    RESET_SCOPES,
    SCALE_CHOICES,
    Arrangement,
    StudioState,
    pitch_rows,
)
from synthlab.synth import Patch, preset
from tests.studio.song_helpers import load_section

# --- field lists are derived, not hand-synced -------------------


def test_every_arrangement_field_has_a_lane():
    assert all("lane" in f.metadata for f in fields(Arrangement))


def test_arrangement_schema_node_matches_lane_tags():
    """schema.py already asserts (at import time, building schema.nodes())
    that its ARRANGEMENT node covers every Arrangement field; this pins
    the other half so the two can never quietly drift apart again: every
    one of those fields also carries a lane tag, and the tagged set is
    exactly the schema node's field set — the one thing
    `_merge_arrangement` (routes/patch.py) and `_drums_from_form`
    (routes/drums.py) lean on to derive their keep-sets instead of
    hand-copying field-name tuples."""
    from synthlab.studio import schema

    tagged = {f.name for f in fields(Arrangement) if "lane" in f.metadata}
    assert tagged == {f.name for f in fields(Arrangement)}
    assert tagged == set(schema.nodes().ARRANGEMENT.fields)


def test_state_roundtrip_covers_every_attr(tmp_path):
    """StudioState does not hand-list its persisted attributes
    (schema.PAYLOAD is the one declaration of what gets saved, and
    state.py *reads* that declaration instead of keeping its own copy)
    — so this pins schema.PAYLOAD's fields against what
    `_payload()` actually emits, rather than against a second, parallel
    `StudioState`-only field table. "v" is the one payload key that isn't
    part of the schema (added at serialization time)."""
    from synthlab.studio import schema

    s = StudioState(tmp_path)
    # PAYLOAD_OPTIONAL (the launcher's `perform_slots`) is absent while empty
    assert set(schema.nodes().PAYLOAD.fields) - set(schema.PAYLOAD_OPTIONAL) == (
        set(s._payload()) - {"v"}
    )


def test_defaults_when_no_state_file(tmp_path):
    s = StudioState(tmp_path)
    assert s.patch.name == "init"
    assert s.arrangement.bpm == 124.0
    assert s.arrangement.cells == [["A1", 0], ["A1", 2]]
    assert s.slot_a is None and s.slot_b is None
    assert len(pitch_rows(Arrangement())) == 6


def test_save_and_reload_roundtrip(tmp_path):
    s = StudioState(tmp_path)
    s.patch = preset("bass_reese")
    s.arrangement = Arrangement(bpm=140.0, cells=[["C2", 0], ["E2", 4]])
    s.slot_a = preset("bass_acid")
    s.save()
    s2 = StudioState(tmp_path)
    assert s2.patch == preset("bass_reese")
    assert s2.arrangement.bpm == 140.0
    assert s2.arrangement.cells == [["C2", 0], ["E2", 4]]
    assert s2.slot_a == preset("bass_acid")
    assert s2.slot_b is None


def test_old_state_without_curves_loads_fine(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "patch": None,
                "arrangement": {},
                "slot_a": None,
                "slot_b": None,
            }
        ),
        encoding="utf-8",
    )
    s = StudioState(tmp_path)
    assert s.curves == {}


def test_curves_roundtrip(tmp_path):
    s = StudioState(tmp_path)
    s.curves = {"cutoff": [[0.0, 0.0], [1.0, 1.0]]}
    s.save()
    s2 = StudioState(tmp_path)
    assert s2.curves == {"cutoff": [[0.0, 0.0], [1.0, 1.0]]}


def test_corrupt_state_falls_back_to_defaults(tmp_path):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    s = StudioState(tmp_path)
    assert s.patch.name == "init"


def test_atomic_save_leaves_valid_json(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(data) == {
        "v",
        "patch",
        "arrangement",
        "slot_a",
        "slot_b",
        "slot_prev",
        "curves",
        "breed_tiles",
        "audition",
        "vs_beat",
        "bass_root",
        "macros",
        "breed_starred",
        "breed_history",
        "breed_wildness",
        "breed_depth",
        "stack_prev",
        "fx_drums",
        "perform_parts",
        "baselines",
        "xy_axes",
        "xy_gesture",
        "layers",
        "active_layer",
        "main_layer",
        "layer_focus",
        "grid_span",
        "fx_master",
        "song",
        "drum_bank",
        "active_slot",
    }


def test_xy_gesture_roundtrip(tmp_path):
    s = StudioState(tmp_path)
    assert s.xy_gesture == []
    s.xy_gesture = [[0.0, 0.1, 0.2], [0.5, 0.6, 0.7]]
    s.save()
    s2 = StudioState(tmp_path)
    assert s2.xy_gesture == [[0.0, 0.1, 0.2], [0.5, 0.6, 0.7]]


def test_state_uses_the_shared_sanitizer():
    """StudioState no longer carries its own _safe — it's a thin caller of
    the shared files.safe_name() (see test_files.py for the sanitizer's
    own tests)."""
    from synthlab.studio.files import safe_name

    assert not hasattr(StudioState, "_safe")
    assert safe_name("con") == "untitled"


def test_old_state_json_still_loads(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text())
    for k in ("velocities", "drums", "swing", "root", "scale_name"):
        data["arrangement"].pop(k, None)
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert s2.arrangement.swing == 0.0  # defaults fill in


def test_presets_crud(tmp_path):
    s = StudioState(tmp_path)
    s.patch = preset("bass_reese")
    s.save_preset("My Bass! (v2)")
    assert s.list_presets() == ["My Bass! (v2)"]
    s.patch = preset("pad_warm")
    s.load_preset("My Bass! (v2)")
    # saving as a name makes that the sound's name (it used to keep the old one)
    from dataclasses import replace

    assert s.patch == replace(preset("bass_reese"), name="My Bass! (v2)")
    s.delete_preset("My Bass! (v2)")
    assert s.list_presets() == []


def test_preset_meta_saved_and_listed(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("warm bass")
    metas = s.list_presets_meta()
    assert metas[0]["name"] == "warm bass"
    assert len(metas[0]["spectrum"]) == 24 and metas[0]["tags"]
    assert metas[0]["starred"] is False
    s.toggle_star("warm bass")
    assert s.list_presets_meta()[0]["starred"] is True
    s.delete_preset("warm bass")
    assert s.list_presets_meta() == []


def test_meta_backfills_for_legacy_preset(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("old one")
    (s.preset_dir() / "old one.meta.json").unlink()  # simulate legacy
    metas = s.list_presets_meta()
    assert metas[0]["tags"] and len(metas[0]["spectrum"]) == 24


def test_meta_suffix_names_cannot_collide(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("foo.meta")
    names = [m["name"] for m in s.list_presets_meta()]
    assert names == ["foo"]  # normalized, visible, no orphan
    s.delete_preset("foo")
    assert s.list_presets_meta() == []


def test_all_zero_meta_backfills(tmp_path):
    import json

    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("quiet")
    mp = s.preset_dir() / "quiet.meta.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["spectrum"] = [0.0] * 24
    del m["verified"]
    mp.write_text(json.dumps(m), encoding="utf-8")
    metas = s.list_presets_meta()
    assert max(metas[0]["spectrum"]) > 0.0


def test_starred_preset_lists_first(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("aaa first")
    s.save_preset("zzz second")
    s.toggle_star("zzz second")
    metas = s.list_presets_meta()
    assert metas[0]["name"] == "zzz second"
    assert metas[0]["starred"] is True


def test_all_zero_verified_meta_is_terminal_and_renders_once(tmp_path, monkeypatch):
    import json

    import synthlab.studio.state as state_mod
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("genuinely silent")
    mp = s.preset_dir() / "genuinely silent.meta.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["spectrum"] = [0.0] * 24
    m["verified"] = True
    m["v"] = 2
    mp.write_text(json.dumps(m), encoding="utf-8")

    calls = {"n": 0}
    real_preset_meta = state_mod.preset_meta

    def counting_preset_meta(patch):
        calls["n"] += 1
        return real_preset_meta(patch)

    monkeypatch.setattr(state_mod, "preset_meta", counting_preset_meta)

    s.list_presets_meta()
    s.list_presets_meta()
    assert calls["n"] == 0


def test_v1_meta_backfills_to_v2_on_listing(tmp_path):
    import json

    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("legacy v1")
    mp = s.preset_dir() / "legacy v1.meta.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    del m["v"]  # simulate a pre-migration meta file
    mp.write_text(json.dumps(m), encoding="utf-8")

    metas = s.list_presets_meta()
    assert metas[0]["tags"] and len(metas[0]["spectrum"]) == 24

    saved = json.loads(mp.read_text(encoding="utf-8"))
    assert saved["v"] == 2
    assert saved["verified"] is True


def test_corrupt_meta_backfill_renders_once_then_is_terminal(tmp_path, monkeypatch):
    import synthlab.studio.state as state_mod
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("corrupt me")
    mp = s.preset_dir() / "corrupt me.meta.json"
    mp.write_text("{not json", encoding="utf-8")

    calls = {"n": 0}
    real_preset_meta = state_mod.preset_meta

    def counting_preset_meta(patch):
        calls["n"] += 1
        return real_preset_meta(patch) | {"spectrum": [0.0] * 24}

    monkeypatch.setattr(state_mod, "preset_meta", counting_preset_meta)

    s.list_presets_meta()
    s.list_presets_meta()
    assert calls["n"] == 1


def test_toggle_star_on_nonexistent_preset_creates_no_meta_file(tmp_path):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.toggle_star("does not exist")
    assert not (s.preset_dir() / "does not exist.meta.json").exists()
    assert s.list_presets() == []


def test_toggle_star_does_not_touch_other_presets_meta(tmp_path):
    import time

    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("aaa first")
    s.save_preset("bbb second")
    other_meta = s.preset_dir() / "bbb second.meta.json"
    before = other_meta.stat().st_mtime_ns

    time.sleep(0.01)
    s.toggle_star("aaa first")

    assert other_meta.stat().st_mtime_ns == before
    metas = {m["name"]: m for m in s.list_presets_meta()}
    assert metas["aaa first"]["starred"] is True
    assert metas["bbb second"]["starred"] is False


def test_toggle_star_avoids_full_scan(tmp_path, monkeypatch):
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    s.save_preset("aaa first")
    s.save_preset("bbb second")

    def boom():
        raise AssertionError("toggle_star should not do a full preset scan")

    monkeypatch.setattr(s, "list_presets_meta", boom)
    s.toggle_star("aaa first")  # should not raise
    monkeypatch.undo()
    metas = s.list_presets_meta()
    assert any(m["name"] == "aaa first" and m["starred"] for m in metas)


# --- tolerant / validated loading -----------------------------


def test_unknown_patch_field_keeps_session(tmp_path):
    from dataclasses import replace

    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=432.0)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["patch"]["retired_knob"] = 1
    (tmp_path / "state.json").write_text(json.dumps(data))
    assert StudioState(tmp_path).patch.cutoff == 432.0


def test_corrupt_state_is_backed_up(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    StudioState(tmp_path)
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_corrupt_state_backup_preserves_the_original_bytes(tmp_path):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    StudioState(tmp_path)
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_state_has_version(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    # v3: the song model replaced v2's scenes + scene running order
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["v"] == 3


def test_loaded_patch_is_clamped(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["patch"]["unison"] = 100_000_000
    data["patch"]["cutoff"] = float("nan")
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert s2.patch.unison <= 16
    import math

    assert math.isfinite(s2.patch.cutoff)


def test_loaded_drum_params_drop_unknown_voices_and_keys_and_clamp(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["arrangement"]["drum_params"] = {
        "kick": {"decay": 1e9, "not_a_param": 1},
        "not_a_voice": {"decay": 0.5},
    }
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert "not_a_voice" not in s2.arrangement.drum_params
    assert "not_a_param" not in s2.arrangement.drum_params["kick"]
    assert s2.arrangement.drum_params["kick"]["decay"] <= 1.2


def test_loaded_xy_axes_falls_back_when_invalid(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["xy_axes"] = ["nope", "cutoff"]
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert s2.xy_axes == ["cutoff", "resonance"]


def test_loaded_curves_drop_uncurvable_keys_and_clamp_points(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["curves"] = {
        "cutoff": [[-5.0, 2.0], [0.5, 0.5]],
        "not_curvable": [[0.0, 0.0], [1.0, 1.0]],
    }
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert "not_curvable" not in s2.curves
    assert s2.curves["cutoff"][0] == [0.0, 1.0]


def test_loaded_root_falls_back_when_unknown(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["arrangement"]["root"] = "Z9"
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert s2.arrangement.root == "A1"


def _v2_state(tmp_path, scenes: list) -> None:
    """A v2 state.json (scenes + a running order) for the migration."""
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data |= {"v": 2, "scenes": scenes, "song": []}
    (tmp_path / "state.json").write_text(json.dumps(data))


def test_loaded_scenes_and_starred_are_capped(tmp_path):
    from synthlab.studio import limits

    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["v"], data["song"] = 2, []  # scenes are a v2 file's
    data["scenes"] = [{"patch": s.patch.to_json(), "arr": {}} for _ in range(40)]
    data["breed_starred"] = [f"id{i}" for i in range(200)]
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    # each migrated scene is a section: the legacy cap holds on the way in
    assert len(s2.song["sections"]) == limits.MAX_SCENES
    assert len(s2.breed_starred) <= limits.MAX_STARRED


def test_old_v1_state_without_a_v_key_still_loads(tmp_path):
    """A file saved before STATE_VERSION existed has no "v" key at all —
    it must load exactly like a current-version file, old field names and
    all."""
    legacy = {
        "patch": None,
        "arrangement": {"bpm": 133.0, "cells": [["C2", 0]]},
        "slot_a": None,
        "slot_b": None,
    }
    (tmp_path / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
    s = StudioState(tmp_path)
    assert s.arrangement.bpm == 133.0
    assert s.patch.name == "init"


# --- hostile stored state: validate at the point of use ------------------
# A baseline (revert), a scene (load_scene), or a preset (load_preset) is
# just stored JSON until something applies it — each of those application
# points validates directly, so it can't matter how the hostile value
# arrived (a crafted import, a hand-edited file, or code that puts a
# baseline/scene there some other way in the future).


def test_revert_clamps_a_hostile_baseline_patch(tmp_path):
    s = StudioState(tmp_path)
    hostile = {**s.patch.to_json(), "unison": 1e8, "attack": 1e7}
    s.baselines["sound"] = {
        "label": "hostile",
        "data": {"patch": hostile, "curves": {}},
    }
    assert s.revert("sound")
    assert s.patch.unison <= 16
    assert s.patch.attack <= 20.0


def test_revert_clamps_a_hostile_baseline_drum_params(tmp_path):
    s = StudioState(tmp_path)
    s.baselines["kit"] = {
        "label": "hostile",
        "data": {
            "arr.drum_params": {"kick": {"sr": 1e12, "decay": 1e9}},
            "arr.drum_mix": {},
        },
    }
    assert s.revert("kit")
    assert "sr" not in s.arrangement.drum_params.get("kick", {})
    assert s.arrangement.drum_params["kick"]["decay"] <= 1.2


def test_load_preset_clamps_a_hostile_saved_file(tmp_path):
    s = StudioState(tmp_path)
    preset_dir = s.preset_dir()
    preset_dir.mkdir(parents=True, exist_ok=True)
    hostile = {**s.patch.to_json(), "unison": 1e8, "attack": 1e7}
    (preset_dir / "hostile.json").write_text(json.dumps(hostile), encoding="utf-8")
    s.load_preset("hostile")
    assert s.patch.unison <= 16
    assert s.patch.attack <= 20.0


def test_load_scene_clamps_a_hostile_arrangement(tmp_path):
    _v2_state(
        tmp_path,
        [
            {
                "name": "hostile",
                "patch": Patch().to_json(),
                "arr": {
                    "drum_params": {"kick": {"sr": 1e12, "decay": 1e9}},
                    "bpm": 999999,
                },
                "curves": {},
                "layers": [],
                "main_muted": False,
                "main_fx": [],
                "master_fx": [],
                "fx_drums": [],
            }
        ],
    )
    s = StudioState(tmp_path)
    assert load_section(s, 0)
    # v3: the first scene supplies the song's kit and tempo, so its hostile
    # values do reach the loop — clamped on the way into the song
    assert "sr" not in s.arrangement.drum_params["kick"]
    assert s.arrangement.drum_params["kick"]["decay"] == 1.2
    assert s.arrangement.bpm == 200.0


def test_loaded_scene_arrangement_is_clamped_at_apply_time(tmp_path):
    """Loading itself (not just load_scene) must clamp a scene's arr — the
    song migrated from it should never hold the hostile value in the first
    place."""
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["v"], data["song"] = 2, []  # scenes are a v2 file's
    data["scenes"] = [
        {
            "name": "hostile",
            "patch": s.patch.to_json(),
            "arr": {"drum_params": {"kick": {"sr": 1e12, "decay": 1e9}}},
            "curves": {},
            "layers": [],
        }
    ]
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert len(s2.song["sections"]) == 1
    assert '"sr"' not in json.dumps(s2.song)


def test_scope_set_replaces_a_json_list_with_default(tmp_path):
    """The same list-impersonating-automation gap params.clamp closes
    for a fresh import must also be closed for a value applied through
    _scope_set (revert/reset), since it's a second, independent path onto
    self.patch."""
    s = StudioState(tmp_path)
    hostile = {**s.patch.to_json(), "cutoff": [1_000_000_000.0]}
    s.baselines["sound"] = {
        "label": "hostile",
        "data": {"patch": hostile, "curves": {}},
    }
    assert s.revert("sound")
    assert isinstance(s.patch.cutoff, float)
    import math

    assert math.isfinite(s.patch.cutoff)


# --- hostile stored state: close the class, not just the instances -------
# The same shape of bug can sit in many places (layers, state.* scopes
# beyond macros/perform_parts, and most Arrangement fields), so there is
# one cleaner per untrusted-input shape and every loading path shares it
# instead of each guarding its own corner.


def test_load_preset_layers_are_clamped(tmp_path):
    """Finding A: load_preset copied a format-2 preset's `layers` in raw.
    Those patches reach the live patch via select_layer, and reach a
    render via other_layers()/build_song — both unclamped."""
    s = StudioState(tmp_path)
    preset_dir = s.preset_dir()
    preset_dir.mkdir(parents=True, exist_ok=True)
    hostile_patch = {
        **s.patch.to_json(),
        "unison": 1e8,
        "attack": 1e7,
        "cutoff": [1e9],
    }
    payload = {
        "format": 2,
        "patch": s.patch.to_json(),
        "layers": [
            {
                "patch": hostile_patch,
                "cells": [["A1", 0]],
                "muted": False,
                "gain": 1.0,
                "fx": [],
            }
        ],
        "main_fx": [],
        "curves": {},
    }
    (preset_dir / "hostile.json").write_text(json.dumps(payload), encoding="utf-8")
    s.load_preset("hostile")
    # the one "other" lane (active_layer defaults to 0) took layers[0]
    other = next(i for i in range(3) if i != s.active_layer)
    stashed = s.layers[other]["patch"]
    assert stashed["unison"] <= 16
    assert stashed["attack"] <= 20.0
    import math

    assert math.isfinite(stashed["cutoff"])
    assert isinstance(stashed["cutoff"], float)
    s.select_layer(other)
    assert s.patch.unison <= 16
    assert s.patch.attack <= 20.0
    assert math.isfinite(s.patch.cutoff)


def test_scene_layers_with_junk_entries_dont_crash_load_scene(tmp_path):
    """Finding A: a scene's layers = ["junk", 5] used to reach
    load_scene()'s direct `self.layers[i] = others.pop(0)` and crash the
    next thing that read that lane as a dict."""
    _v2_state(
        tmp_path,
        [
            {
                "name": "junk layers",
                "patch": Patch().to_json(),
                "arr": {},
                "curves": {},
                "layers": ["junk", 5],
                "main_muted": False,
                "main_fx": [],
                "master_fx": [],
                "fx_drums": [],
            }
        ],
    )
    s = StudioState(tmp_path)
    assert load_section(s, 0)
    for lay in s.layers:
        assert isinstance(lay, dict)
        assert isinstance(lay.get("fx"), list)
        assert isinstance(lay.get("cells"), list)


def test_revert_clamps_hostile_macros_and_perform_parts(tmp_path):
    """Finding B: _apply validated top-level macros/perform_parts but a
    baseline for those scopes skipped it — revert() set them raw and the
    next page render crashed on macros.html's `m.value`."""
    s = StudioState(tmp_path)
    s.baselines["macros"] = {"label": "x", "data": {"state.macros": [5, "x"]}}
    s.baselines["perform"] = {
        "label": "x",
        "data": {"state.perform_parts": ["zzz", 1]},
    }
    assert s.revert("macros")
    assert s.revert("perform")
    assert len(s.macros) == 4
    for m in s.macros:
        assert isinstance(m, dict) and isinstance(m["value"], float)
    assert s.perform_parts and all(p in ("synth", "drums") for p in s.perform_parts)


def test_apply_and_scope_set_use_the_same_state_cleaners(tmp_path):
    """_apply (loading) and _scope_set (revert) must not be able to drift:
    every resettable key validates against the very node the payload
    schema uses for it (one schema, no separate table of cleaners)."""
    from synthlab.studio import schema

    n = schema.nodes()
    state_keys = {
        k.split(".", 1)[1]
        for _label, keys in RESET_SCOPES.values()
        for k in keys
        if k.startswith("state.")
    }
    assert state_keys == {
        "fx_master",
        "fx_drums",
        "macros",
        "perform_parts",
        "breed_tiles",
        "breed_starred",
        "breed_history",
        "song",
        "perform_slots",  # the launcher, in the Sequence scope
    }
    for name in state_keys:
        assert n.SCOPES[f"state.{name}"] is n.PAYLOAD.fields[name]
    for k, node in n.SCOPES.items():
        if k.startswith("arr."):
            assert node is n.ARRANGEMENT.fields[k[4:]]


def test_revert_clamps_hostile_arrangement_fields(tmp_path):
    """Finding C: _clean_arrangement didn't validate scale_name,
    arp_octaves, swing and several other fields — a revert (or a
    top-level import) could put a bogus scale_name or a swing of 1e9
    live, and arp_octaves=1e6 would try to build a million-entry arp
    sequence."""
    s = StudioState(tmp_path)
    s.baselines["sequence"] = {
        "label": "x",
        "data": {
            "arr.scale_name": "bogus",
            "arr.arp_octaves": 1e6,
            "arr.swing": 1e9,
        },
    }
    assert s.revert("sequence")
    assert s.arrangement.scale_name in SCALE_CHOICES
    assert s.arrangement.arp_octaves <= 3
    assert s.arrangement.swing <= 0.6


def test_top_level_import_clamps_arrangement_fields(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["arrangement"]["scale_name"] = "bogus"
    data["arrangement"]["drum_swing"] = 1e9
    data["arrangement"]["drum_humanize"] = -1e9
    data["arrangement"]["drum_mix"] = {"kick": {"level": 1e9, "mute": "yes"}}
    data["arrangement"]["clips"] = [
        {"clip": "../../etc/passwd", "at_bar": -5, "gain": 1e9}
    ]
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)
    assert s2.arrangement.scale_name in SCALE_CHOICES
    assert 0.0 <= s2.arrangement.drum_swing <= 0.6
    assert 0.0 <= s2.arrangement.drum_humanize <= 1.0
    assert s2.arrangement.drum_mix["kick"]["level"] <= 1.5
    assert s2.arrangement.drum_mix["kick"]["mute"] is True
    assert "/" not in s2.arrangement.clips[0]["clip"]
    assert s2.arrangement.clips[0]["at_bar"] >= 0
    assert s2.arrangement.clips[0]["gain"] <= 1.5


# --- hostile containers, non-finite numbers, song ------------------------
# The same shape of bug one level up: a cleaner assuming its INPUT is
# already the right container type before indexing/iterating it, and
# unguarded int() calls that raise OverflowError (not the ValueError/
# TypeError a try/except catches) on JSON's `1e400`.


def test_clean_macros_survives_a_non_list_targets():
    from synthlab.studio import schema

    out = schema.conform(
        [{"targets": 5}, {"targets": "x"}, {"targets": {}}, 1], schema.nodes().MACROS
    )
    assert len(out) == 4
    for m in out:
        assert isinstance(m["targets"], list)


def test_clean_breed_tiles_survives_non_list_spectrum_and_badges():
    from synthlab.studio import schema

    out = schema.conform(
        [{"id": "a", "spectrum": 5, "badges_genes": {}}, {"id": "b", "spectrum": "x"}],
        schema.nodes().TILES,
    )
    assert len(out) == 2
    for t in out:
        assert isinstance(t["spectrum"], list)
        assert isinstance(t["badges_genes"], list)


def test_clean_chain_survives_a_non_list_units():
    from synthlab.studio.fxrack import clean_chain

    assert clean_chain(5) == []
    assert clean_chain("bogus") == []
    assert clean_chain({"kind": "reverb"}) == []  # a dict, not a list of dicts


def test_revert_with_hostile_container_types_does_not_crash(tmp_path):
    """Finding 1's probe, through the actual application path
    (/fragment/reset via revert -> _scope_set)."""
    s = StudioState(tmp_path)
    s.baselines["macros"] = {
        "label": "x",
        "data": {"state.macros": [{"targets": 5}, 1, "x", None]},
    }
    s.baselines["board"] = {
        "label": "x",
        "data": {
            "state.breed_tiles": [{"id": "a", "spectrum": 5, "badges_genes": 3}],
            "state.breed_starred": [],
            "state.breed_history": [],
        },
    }
    s.baselines["fx"] = {"label": "x", "data": {"layer_fx": 5, "state.fx_master": 5}}
    assert s.revert("macros")
    assert s.revert("board")
    assert s.revert("fx")
    assert len(s.macros) == 4
    assert isinstance(s.layers[s.active_layer]["fx"], list)
    assert isinstance(s.fx_master, list)


def test_arp_octaves_infinity_does_not_crash_import(tmp_path):
    """Finding 2's probe: JSON's 1e400 parses to inf, and int(inf) raises
    OverflowError — not caught by the ValueError/TypeError every
    coercion in this file used to guard against."""
    from synthlab.studio.state import _clean_arrangement

    arr = _clean_arrangement(
        {
            "arp_octaves": float("inf"),
            "cells": [["A1", float("inf")]],
            "slides": [float("-inf")],
            "clips": [{"clip": "x", "at_bar": float("inf"), "gain": float("inf")}],
        }
    )
    # non-finite input falls back to the default, not the clamp bound
    assert arr.arp_octaves == 1
    assert arr.cells == []
    assert arr.slides == []
    assert arr.clips[0]["at_bar"] >= 0
    assert arr.clips[0]["gain"] <= 1.5


def test_state_json_with_infinite_value_still_starts(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    data["arrangement"]["arp_octaves"] = float("inf")
    data["active_layer"] = float("inf")
    (tmp_path / "state.json").write_text(json.dumps(data))
    s2 = StudioState(tmp_path)  # must not raise OverflowError
    assert s2.arrangement.arp_octaves == 1
    assert 0 <= s2.active_layer < len(s2.layers)


def test_load_project_rejects_infinite_json_without_crashing(tmp_path):
    """json.dumps of inf produces the literal `Infinity`, which Python's
    json module reads back as float('inf') — real, not a test artifact."""
    s = StudioState(tmp_path)
    proj = s.project_payload()
    proj["arrangement"]["arp_octaves"] = float("inf")
    proj["breed_tiles"] = [{"id": "x", "centroid": float("inf")}]
    proj["active_layer"] = float("inf")
    s.load_project(proj)  # must not raise OverflowError
    assert s.arrangement.arp_octaves == 1
    assert 0 <= s.active_layer < len(s.layers)


def test_clean_song_drops_bad_entries_and_bounds_scene_and_repeats():
    from synthlab.studio.state import _clean_song

    out = _clean_song(
        [
            {"scene": "bogus", "repeats": 1},
            {"scene": 0, "repeats": 1e9},
            {"scene": 5, "repeats": 1},  # out of range for n_scenes=2
            "junk",
            {"scene": 1.0, "repeats": -3},
        ],
        n_scenes=2,
    )
    assert out == [{"scene": 0, "repeats": 16}, {"scene": 1, "repeats": 1}]


def test_import_song_with_bad_scene_index_is_cleaned(tmp_path):
    """Finding 3's probe: song=[{"scene": "bogus"}] used to reach
    /view/perform and /export/song.wav raw and 500."""
    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    client = TestClient(create_app(data_dir=tmp_path))
    proj = client.get("/project.json").json()
    proj["song"] = [{"scene": "bogus"}, {"scene": 0, "repeats": "bogus"}]
    r = client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.get("/view/perform").status_code == 200
    # both entries dropped ("bogus" isn't a scene index, and this project
    # has no scenes at all), so — v3 — the migration makes the working loop
    # the one section, "Loop 1", which exports cleanly
    song = client.app.state.studio.song
    assert [s["name"] for s in song["sections"]] == ["Loop 1"]
    assert client.get("/export/song.wav").status_code == 200


def test_valid_pitch_rejects_an_absurd_octave():
    from synthlab.studio.schema import valid_pitch as _valid_pitch

    assert _valid_pitch("C4") is True
    assert _valid_pitch("C99999") is False
    assert _valid_pitch("C-99999") is False


def test_clean_grid_notes_keeps_cells_outside_the_current_grid(tmp_path):
    """Finding 5, the regression: a cell/velocity/slide outside the
    arrangement's *current* bars x steps_per_bar must survive cleaning —
    the live UI hides it on a shrunk grid rather than deleting it — but a
    step past the largest grid the UI can ever produce (128) is garbage
    and still gets dropped."""
    from synthlab.studio.state import _clean_arrangement

    arr = _clean_arrangement(
        {
            "bars": 1,
            "steps_per_bar": 4,  # current grid: 4 steps
            "cells": [["A1", 12], ["A1", 200]],  # 12 fits in the max grid, 200 doesn't
            "velocities": {"12": 0.5, "200": 0.5},
            "slides": [12, 200],
        }
    )
    assert arr.cells == [["A1", 12]]
    assert arr.velocities == {"12": 0.5}
    assert arr.slides == [12]


def test_revert_restores_a_cell_hidden_by_a_shrunk_grid(tmp_path):
    """The exact scenario from the regression report: baseline bars=4
    with a cell at step 12, shrink to bars=1, revert -> the cell is back
    (and the grid is back to 4 bars too)."""
    from dataclasses import replace

    s = StudioState(tmp_path)
    s.arrangement = replace(s.arrangement, bars=4, steps_per_bar=4, cells=[["A1", 12]])
    s.mark_baseline("sequence", "big")
    s.arrangement = replace(s.arrangement, bars=1, cells=[])
    assert s.revert("sequence")
    assert s.arrangement.bars == 4
    assert s.arrangement.cells == [["A1", 12]]


def test_load_project_is_atomic_on_failure(tmp_path):
    """Out-of-scope fix: _apply mutates self directly and isn't atomic —
    a project that fails partway through cleaning must leave the session
    exactly as it was, not with e.g. the new patch adopted but the old
    arrangement kept."""
    s = StudioState(tmp_path)
    from dataclasses import replace as dc_replace

    s.patch = dc_replace(s.patch, cutoff=432.0)
    s.loop_to_section("kept")
    s.macros[0]["value"] = 0.25
    original_patch = s.patch
    original_arrangement = s.arrangement
    original_payload = json.dumps(s._payload(), sort_keys=True)
    bad_project = {
        "format": StudioState.PROJECT_FORMAT,
        "v": 2,
        "patch": {**s.patch.to_json(), "cutoff": 999.0},
        # "arrangement" deliberately missing -> _apply raises KeyError
        # partway through, after self.patch was already reassigned
    }
    import pytest

    with pytest.raises(ValueError):
        s.load_project(bad_project)
    assert s.patch == original_patch
    assert s.patch.cutoff == 432.0
    assert s.arrangement == original_arrangement
    assert json.dumps(s._payload(), sort_keys=True) == original_payload


def test_apply_rolls_back_a_failure_after_it_started_assigning(tmp_path, monkeypatch):
    """The schema conforms before assigning, so most rejections change
    nothing by construction — but _apply_or_restore must still undo a
    failure that lands after assignments began (here: mark_baselines,
    the last step, raising)."""
    import pytest

    s = StudioState(tmp_path)
    s.loop_to_section("kept")
    before = json.dumps(s._payload(), sort_keys=True)
    proj = s.project_payload()
    proj["patch"]["cutoff"] = 999.0
    proj["scenes"] = []
    proj["baselines"] = {}  # forces the mark_baselines branch

    def boom(self, scopes, label=""):
        raise RuntimeError("late failure")

    monkeypatch.setattr(StudioState, "mark_baselines", boom)
    with pytest.raises(ValueError):
        s.load_project(proj)
    assert json.dumps(s._payload(), sort_keys=True) == before
