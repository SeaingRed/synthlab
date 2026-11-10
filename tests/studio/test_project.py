import copy
import itertools
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import song_bridge
from synthlab.studio.server import create_app
from synthlab.studio.state import STATE_VERSION, StudioState
from tests.studio.song_helpers import load_section


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


def test_export_is_a_downloadable_project(tmp_path):
    r = make_client(tmp_path).get("/project.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.synthlab.json"')
    data = r.json()
    # the project's own version IS the state schema version:
    # a project export is "everything save() writes", version included.
    assert data["format"] == "synthlab-project" and data["v"] == STATE_VERSION
    assert "patch" in data and "arrangement" in data and "drum_bank" in data


def test_import_restores_state_across_lifespan(tmp_path):
    with make_client(tmp_path) as client:
        data = client.get("/project.json").json()
        data["arrangement"]["bpm"] = 97.0
        data["drum_bank"][0]["name"] = "imported"
        r = client.post(
            "/project/import",
            files={"file": ("x.synthlab.json", json.dumps(data), "application/json")},
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"] == "/"
        s: StudioState = client.app.state.studio
        assert s.arrangement.bpm == 97.0
    again = StudioState(tmp_path)
    assert again.arrangement.bpm == 97.0
    assert again.drum_bank[0]["name"] == "imported"


def test_import_rejects_junk_and_wrong_format(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/project/import", files={"file": ("x", b"not json", "text/plain")})
    assert r.status_code == 422
    r = client.post(
        "/project/import",
        files={"file": ("x", json.dumps({"format": "nope"}), "application/json")},
    )
    assert r.status_code == 422
    assert client.post("/project/import").status_code == 422


def test_editor_has_export_and_import_controls(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'href="/project.json"' in html
    assert 'hx-post="/project/import"' in html
    assert 'type="file"' in html


def test_import_clamps_structural_fields(tmp_path):
    client = make_client(tmp_path)
    data = client.get("/project.json").json()
    data["arrangement"]["bpm"] = 9999
    data["arrangement"]["bars"] = 99
    data["arrangement"]["steps_per_bar"] = 7
    r = client.post(
        "/project/import",
        files={"file": ("x", json.dumps(data), "application/json")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    arr = client.app.state.studio.arrangement
    assert (arr.bpm, arr.bars, arr.steps_per_bar) == (200.0, 1, 4)


@pytest.mark.parametrize(
    "patch_over",
    [
        {"unison": 100_000_000},
        {"attack": 1e7},
        {"cutoff": float("nan")},
        {"osc1": "not-a-shape"},
    ],
)
def test_import_clamps_patch(client, patch_over):
    proj = client.get("/project.json").json()
    proj["patch"].update(patch_over)
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    p = client.app.state.studio.patch
    assert p.unison <= 16 and p.attack <= 20 and math.isfinite(p.cutoff)


def test_import_rejects_bad_drum_params_and_axes(client):
    proj = client.get("/project.json").json()
    proj["arrangement"]["drum_params"] = {"kick": {"sr": 1e12, "decay": 1e9}}
    proj["xy_axes"] = ["nope", "x"]
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    assert client.get("/").status_code == 200
    assert client.get("/view/perform").status_code == 200
    s: StudioState = client.app.state.studio
    assert "sr" not in s.arrangement.drum_params.get("kick", {})
    assert s.arrangement.drum_params["kick"]["decay"] <= 1.2
    assert s.xy_axes == ["cutoff", "resonance"]


# --- hostile baselines: validate at the point of use ----------------------
# Checking only a baseline dict's *shape* (RESET_SCOPES membership), never
# its contents, would let revert()/_scope_set() apply that content
# unclamped. A baseline is stored JSON until revert() runs it — so it is
# validated at that point, not at import time, and it can't matter how a
# hostile value got in (imported project, hand-edited state.json, ...).


def test_import_then_revert_clamps_a_hostile_baseline_patch(client):
    proj = client.get("/project.json").json()
    hostile_patch = {**proj["patch"], "unison": 1e8, "attack": 1e7}
    proj["baselines"] = {
        "sound": {"label": "hostile", "data": {"patch": hostile_patch, "curves": {}}}
    }
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    s: StudioState = client.app.state.studio
    assert s.revert("sound")
    assert s.patch.unison <= 16
    assert s.patch.attack <= 20.0


def test_import_then_revert_clamps_a_hostile_baseline_drum_params(client):
    proj = client.get("/project.json").json()
    proj["baselines"] = {
        "kit": {
            "label": "hostile",
            "data": {
                "arr.drum_params": {"kick": {"sr": 1e12, "decay": 1e9}},
                "arr.drum_mix": {},
            },
        }
    }
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    s: StudioState = client.app.state.studio
    assert s.revert("kit")
    assert "sr" not in s.arrangement.drum_params.get("kick", {})
    assert s.arrangement.drum_params["kick"]["decay"] <= 1.2


def test_import_with_json_list_in_numeric_field_does_not_break_get(client):
    """A list can never be a legitimate value for a scalar Patch field —
    automation is always np.ndarray, only at render time, never JSON — so
    it must be replaced at clamp time, not passed through to break every
    page that reads it."""
    proj = client.get("/project.json").json()
    proj["patch"]["cutoff"] = [1_000_000_000.0]
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    r = client.get("/")
    assert r.status_code == 200
    p = client.app.state.studio.patch
    assert isinstance(p.cutoff, float)
    assert math.isfinite(p.cutoff)
    from synthlab.params import PARAMS

    lo, hi = PARAMS["cutoff"].gene
    assert lo <= p.cutoff <= hi


def test_import_scene_with_hostile_drum_params_is_clamped_on_load(client):
    proj = client.get("/project.json").json()
    # scenes + a running order are a v2 file's: the migration reads them
    proj["v"], proj["song"] = 2, [{"scene": 0, "repeats": 1}]
    proj["scenes"] = [
        {
            "name": "hostile",
            "patch": proj["patch"],
            "arr": {
                **proj["arrangement"],
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
    ]
    client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    s: StudioState = client.app.state.studio
    # the migration conforms the scene before reading it, and the first
    # scene supplies the song's kit and tempo: clamped in the song (belt)
    # and in the loop load_scene builds from it (braces)
    drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    assert drums["drum_params"]["kick"] == {"decay": 1.2}
    assert s.song["bpm"] == 200.0
    assert load_section(s, 0)
    assert "sr" not in s.arrangement.drum_params["kick"]
    assert s.arrangement.drum_params["kick"]["decay"] == 1.2
    assert s.arrangement.bpm == 200.0


# --- hostile values in every class of state ------------------------------


def test_preset_audition_v2_layers_are_clamped(client):
    """Finding A, the third call site: the v2 audition (a
    format-2 preset's ▶) fed the file's raw layers/main_fx/curves straight
    into render_loop, unclamped — now `svc.audition` via `sounds.find`,
    called from routes/presets.py's /preset/{name}/audition.wav route."""
    s: StudioState = client.app.state.studio
    preset_dir = s.preset_dir()
    preset_dir.mkdir(parents=True, exist_ok=True)
    hostile_patch = {**s.patch.to_json(), "unison": 1e8, "cutoff": [1e9]}
    payload = {
        "format": 2,
        "patch": s.patch.to_json(),
        "layers": [
            {
                "patch": hostile_patch,
                "cells": [["A1", 0]],
                "muted": False,
                "gain": 1.0,
                "fx": [{"kind": "bogus-kind", "params": {}}],
            }
        ],
        "main_fx": [{"kind": "reverb", "params": {"mix": 1e9}}],
        "curves": {"cutoff": [["bogus", 2.0]]},
    }
    (preset_dir / "hostile.json").write_text(json.dumps(payload), encoding="utf-8")
    r = client.get("/preset/hostile/audition.wav")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"


def test_load_project_never_raises_on_any_input(client):
    """load_project must always resolve to either a clean import or a
    caught, reported failure — never an uncaught exception that would
    surface as a 500."""
    for junk in (
        {"format": "synthlab-project", "patch": [1e9], "arrangement": "bogus"},
        {"format": "synthlab-project"},
    ):
        r = client.post(
            "/project/import",
            files={"file": ("p.json", json.dumps(junk).encode())},
            follow_redirects=False,
        )
        assert r.status_code in (303, 422)
        assert client.get("/").status_code == 200


# --- the class-level guard: hostile containers -----------------------------
# A fuzzer that only replaces scalar leaves misses the same class of bug
# one level up: a cleaner that assumes its *container* is already the
# right shape (a dict where it expects a list, e.g.) before indexing or
# iterating it. This one also turns whole containers hostile, injects inf
# (JSON's 1e400) among the values, covers more top-level keys
# (song/drum_bank/xy_gesture/curves), and — the real strength — actually
# exercises every scope revert, every scene load and every layer select
# after import, not just a handful of GETs.

_HOSTILE_LEAVES = [1e9, -1e9, "bogus", [1e9], None, {}, float("inf"), 10**400, True]
# Truthy, non-empty containers on purpose: `x or {}` swallows a falsy 0 or
# [] — a pool that only ever produces the int 0 leaves the container
# branch testing nothing (indexing with n % 4 inside an n % 4 == 0 branch).
_HOSTILE_CONTAINERS = [5, "bogus", [1], {"x": 1}, [1e9, "x"], {"bogus": [1]}, 0, []]


def _fuzz(obj, counter, used: set | None = None):
    """Walk a JSON-shaped structure. At each dict/list node, on a 1-in-4
    draw the whole container is replaced by a hostile int/str/list/dict
    instead of being walked further (covers "code assumed this key is
    always a dict/list"); otherwise recurse. Every scalar leaf is always
    replaced. Both pools are drawn in rotation from a shared counter, so a
    run is fully determined by its starting `offset` — reproducible, and
    bounded (no randomness to occasionally roll something pathologically
    slow)."""
    n = next(counter)
    if isinstance(obj, dict | list):
        if n % 4 == 0:
            i = (n // 4) % len(_HOSTILE_CONTAINERS)
            if used is not None:
                used.add(i)
            return copy.deepcopy(_HOSTILE_CONTAINERS[i])
        if isinstance(obj, dict):
            return {k: _fuzz(v, counter, used) for k, v in obj.items()}
        return [_fuzz(v, counter, used) for v in obj]
    return _HOSTILE_LEAVES[n % len(_HOSTILE_LEAVES)]


def _fuzzed_keys(proj: dict) -> list[str]:
    """Every persisted key — derived from the export itself, so a key added
    to _payload() is fuzzed without anyone remembering to list it here."""
    return [k for k in proj if k not in ("format", "v")]


def _seed_project(proj: dict, *, legacy: bool = False) -> dict:
    """Real structure for the fuzz to walk — every key _FUZZED_KEYS names
    gets at least one non-empty, well-formed entry, so a run has
    something to corrupt rather than trivially surviving on empty
    containers. `legacy` seeds a v2 project instead: `scenes` and the
    scene running order where v3 has the song, for the migration."""
    proj["layers"][1] = {
        "patch": dict(proj["patch"]),
        "cells": [["A1", 0]],
        "muted": False,
        "gain": 1.0,
        "fx": [{"kind": "reverb", "params": {"mix": 0.3}}],
    }
    scenes = [
        {
            "name": "scene1",
            "patch": dict(proj["patch"]),
            "arr": dict(proj["arrangement"]),
            "curves": {"cutoff": [[0.0, 0.0], [1.0, 1.0]]},
            "layers": [
                {
                    "patch": dict(proj["patch"]),
                    "cells": [["C2", 1]],
                    "muted": False,
                    "gain": 1.0,
                    "fx": [],
                }
            ],
            "main_muted": False,
            "main_fx": [{"kind": "drive", "params": {}}],
            "master_fx": [],
            "fx_drums": [],
        }
    ]
    arr_keys = (
        "arr.cells",
        "arr.velocities",
        "arr.slides",
        "arr.bars",
        "arr.steps_per_bar",
        "arr.swing",
        "arr.root",
        "arr.scale_name",
        "arr.arp_mode",
        "arr.arp_octaves",
    )
    pattern_keys = (
        "arr.drums",
        "arr.drum_vel",
        "arr.drum_bars",
        "arr.drum_steps",
        "arr.drum_swing",
        "arr.drum_humanize",
    )
    proj["baselines"] = {
        "sound": {"label": "x", "data": {"patch": dict(proj["patch"]), "curves": {}}},
        "kit": {
            "label": "x",
            "data": {
                "arr.drum_params": {"kick": {"decay": 0.5}},
                "arr.drum_mix": {"kick": {"level": 1.0}},
            },
        },
        "sequence": {
            "label": "x",
            "data": {k: proj["arrangement"].get(k.split(".", 1)[1]) for k in arr_keys},
        },
        "pattern": {
            "label": "x",
            "data": {
                k: proj["arrangement"].get(k.split(".", 1)[1]) for k in pattern_keys
            },
        },
        "macros": {"label": "x", "data": {"state.macros": proj["macros"]}},
        "perform": {
            "label": "x",
            "data": {"state.perform_parts": proj["perform_parts"]},
        },
        "board": {
            "label": "x",
            "data": {
                "state.breed_tiles": [
                    {
                        "id": "a",
                        "name": "n",
                        "sub_pct": 1.0,
                        "centroid": 100,
                        "spectrum": [0.1],
                        "badges_genes": [],
                    }
                ],
                "state.breed_starred": ["a"],
                "state.breed_history": [[]],
            },
        },
        "fx": {
            "label": "x",
            "data": {
                "layer_fx": [{"kind": "chorus", "params": {}}],
                "state.fx_master": [],
            },
        },
        "fx_drums": {"label": "x", "data": {"state.fx_drums": []}},
    }
    proj["fx_master"] = [{"kind": "reverb", "params": {"mix": 0.4}}]
    proj["fx_drums"] = [{"kind": "delay", "params": {}}]
    proj["macros"] = [
        {"name": "m", "value": 0.5, "targets": [{"param": "cutoff", "amt": 0.5}]}
    ] * 4
    proj["breed_tiles"] = [
        {
            "id": "x",
            "name": "n",
            "sub_pct": 1.0,
            "centroid": 100,
            "spectrum": [0.1, 0.2],
            "badges_genes": ["g"],
        }
    ]
    proj["xy_gesture"] = [[0.0, 0.1, 0.2], [0.5, 0.6, 0.7]]
    proj["curves"] = {"cutoff": [[0.0, 0.0], [1.0, 1.0]]}
    proj["slot_a"] = dict(proj["patch"])
    proj["slot_b"] = dict(proj["patch"])
    proj["slot_prev"] = dict(proj["patch"])
    proj["breed_starred"] = ["x"]
    proj["breed_history"] = [list(proj["breed_tiles"])]
    proj["active_slot"] = 0
    proj["arrangement"].update(
        cells=[["A1", 0], ["E2", 3]],
        velocities={"0": 0.5},
        slides=[3],
        drums=[["kick", 0], ["hat", 2]],
        drum_vel={"2": 0.5},
        drum_samples={"kick": "take"},
        drum_params={"kick": {"decay": 0.5}},
        drum_mix={"hat": {"level": 0.8, "mute": False}},
        clips=[{"clip": "take", "at_bar": 0, "gain": 1.0}],
    )
    # the song: a legacy scene of this very session, migrated — so it has
    # tracks of every kind with kit, FX and curves, drum and note patterns,
    # and a section with a take for the fuzz to corrupt
    scenes[0]["arr"] = dict(proj["arrangement"])
    v2 = {**proj, "v": 2, "scenes": scenes, "song": [{"scene": 0, "repeats": 2}]}
    if legacy:
        return v2
    proj["song"] = song_bridge.migrate_v2(v2)["song"]
    proj["baselines"]["song"] = {"label": "x", "data": {"state.song": proj["song"]}}
    return proj


def _assert_pages_never_500(client):
    for path in ("/", "/view/perform", "/view/drums", "/view/breed"):
        assert client.get(path).status_code == 200, path
    # a song with nothing valid left to export is a clean 404, not a
    # crash — the class guard is "never 500", not "always something to
    # export"
    assert client.get("/export/song.wav").status_code in (200, 404)
    preview = client.post("/fragment/preview", data={"view": "perform"})
    assert preview.status_code in (200, 422), preview.status_code


@pytest.mark.parametrize("offset", range(4))
def test_import_survives_every_container_and_leaf_turned_hostile(tmp_path, offset):
    """The class-level guard: no matter which JSON node a corrupt/hostile
    project replaces — a whole container or a scalar leaf — with a
    garbage value (including inf), the studio must never 500 and never
    hang, not on import and not on anything import's result feeds into.
    The import must SUCCEED. Only a missing patch/arrangement/
    A-B slot may reject a payload (the schema conforms everything else), and
    the fuzz never deletes keys — so a 422 here means a whole fuzz run
    exercised nothing downstream — a fuzz that 422s every run passes
    against code it should fail. A
    song/scene left with nothing valid may still 404 on export."""
    client = make_client(tmp_path)
    proj = _seed_project(client.get("/project.json").json())

    counter = itertools.count(offset)
    for key in _fuzzed_keys(proj):
        proj[key] = _fuzz(proj[key], counter)

    r = client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:300]
    _assert_pages_never_500(client)

    s: StudioState = client.app.state.studio
    from synthlab.studio.state import RESET_SCOPES

    for scope in RESET_SCOPES:
        s.revert(scope)
        _assert_pages_never_500(client)

    for i in range(len(s.song["sections"])):
        load_section(s, i)  # must not raise, regardless of what it returns
        _assert_pages_never_500(client)

    for i in range(len(s.layers)):
        s.select_layer(i)
        _assert_pages_never_500(client)


@pytest.mark.parametrize("offset", range(4))
def test_a_hostile_v2_project_migrates_and_never_500s(tmp_path, offset):
    """The same fuzz over a v2 project — its scenes and scene running order
    turned hostile too — so the v2 -> v3 migration is fuzzed: the import
    succeeds and nothing it produced 500s."""
    client = make_client(tmp_path)
    proj = _seed_project(client.get("/project.json").json(), legacy=True)
    assert proj["v"] == 2 and "scenes" in proj
    counter = itertools.count(offset)
    for key in _fuzzed_keys(proj):
        proj[key] = _fuzz(proj[key], counter)
    r = client.post(
        "/project/import",
        files={"file": ("p.json", json.dumps(proj).encode())},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:300]
    _assert_pages_never_500(client)
    s: StudioState = client.app.state.studio
    assert "scenes" not in s._payload() and isinstance(s.song, dict)
    for i in range(len(s.song["sections"])):
        load_section(s, i)
        _assert_pages_never_500(client)


@pytest.mark.parametrize("offset", range(4))
def test_state_json_survives_every_container_and_leaf_turned_hostile(tmp_path, offset):
    """Same fuzz, written directly as state.json instead of imported —
    StudioState(tmp_path) must always start, and must start from
    the file: the corrupt-backup fallback is for files that aren't a
    payload at all (test_state_probes covers it), not for hostile values
    inside one."""
    seed_dir = tmp_path / "seed"
    proj = _seed_project(make_client(seed_dir).get("/project.json").json())

    counter = itertools.count(offset)
    for key in _fuzzed_keys(proj):
        proj[key] = _fuzz(proj[key], counter)
    state_data = {k: v for k, v in proj.items() if k != "format"}

    actual_dir = tmp_path / "actual"
    actual_dir.mkdir()
    (actual_dir / "state.json").write_text(json.dumps(state_data), encoding="utf-8")

    StudioState(actual_dir)  # must not raise
    # ...and must have LOADED it, not fallen back to defaults: the schema
    # makes every non-deleted key usable, so a corrupt-backup here means
    # the loader gave up on a file it should have conformed
    assert not list(actual_dir.glob("state.json.corrupt-*"))
    client = make_client(actual_dir)
    _assert_pages_never_500(client)


def test_the_fuzz_really_uses_every_hostile_container(tmp_path):
    """Guards the guard: a fuzz can silently only ever substitute the int
    0. Across the offsets the tests run, every container kind — the
    truthy, non-empty ones included — must actually be injected, and every
    persisted key must be walked."""
    proj = _seed_project(make_client(tmp_path).get("/project.json").json())
    fresh = StudioState(tmp_path / "fresh")._payload()
    assert set(_fuzzed_keys(proj)) == set(fresh) - {"v"}
    used: set = set()
    for offset in range(4):
        counter = itertools.count(offset)
        for key in _fuzzed_keys(proj):
            _fuzz(copy.deepcopy(proj[key]), counter, used)
    assert used == set(range(len(_HOSTILE_CONTAINERS)))
