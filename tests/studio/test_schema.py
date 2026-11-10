"""The persisted-state schema (studio/schema.py), tested by walking it.

Coverage follows the schema automatically: `_example` builds a fully
populated, valid payload from the schema itself (every optional present,
every list non-empty, every partial field filled), and the sweep injects
each hostile value at every node the schema has. Add a field to the schema
and it is fuzzed the next run; add a persisted key without a schema node
and `test_schema_covers_every_persisted_key` fails.
"""

import copy
import json
import math
from dataclasses import fields, replace

import pytest

from synthlab.studio import schema
from synthlab.studio.fxrack import clean_chain
from synthlab.studio.state import RESET_SCOPES, Arrangement, StudioState
from synthlab.synth import Patch
from tests.studio.song_helpers import load_section

N = schema.nodes()
_DELETE = object()  # "remove this key" as a hostile variant
HOSTILES = [
    5,
    -1e9,
    1e9,
    10**400,
    float("inf"),
    float("nan"),
    True,
    "bogus",
    "",
    None,
    [1],
    [[1, 2]],
    [],
    {"x": 1},
    {},
    _DELETE,
]


def _example(node):
    """A valid, fully populated value for `node`."""
    if isinstance(node, schema.Num):
        if node.example is not None:  # its default is "unset", and absent
            return node.example
        d = node.default()
        if d is not None:
            return d
        lo = node.lo if math.isfinite(node.lo) else min(0.0, node.hi)
        return int(lo) if node.integer else float(lo)
    if isinstance(node, schema.Choice):
        return node.default()
    if isinstance(node, schema.Str):
        return "x"
    if isinstance(node, schema.Bool):
        return True
    if isinstance(node, schema.Opt):
        return _example(node.node)
    if isinstance(node, schema.Leaf):
        return copy.deepcopy(node.example)
    if isinstance(node, schema.Tuple):
        return [_example(n) for n in node.nodes]
    if isinstance(node, schema.ListOf):
        return [_example(node.elem)]
    if isinstance(node, schema.FixedList):
        return [_example(node.elem) for _ in range(node.n)]
    if isinstance(node, schema.MapOf):
        return {_example(node.key): _example(node.value)}
    if isinstance(node, schema.Record):
        return {name: _example(child) for name, child in node.fields.items()}
    if isinstance(node, schema.Tagged):  # its first variant, tag included
        rec = next(iter(node.variants.values()))
        return _example(rec)
    raise AssertionError(f"no example for {node!r}")


def _paths(node, path=()):
    """Every (path, node) in the schema, depth-first."""
    yield path, node
    for step, child in node.children():
        yield from _paths(child, (*path, step))


def _inject(data, path, value):
    """`data` with every location `path` names replaced by `value`
    ("*" = every element, "?" = an Opt's value in place)."""
    if not path:
        return value
    step, rest = path[0], path[1:]
    if step == "?":
        return _inject(data, rest, value)
    if isinstance(data, dict):
        keys = list(data) if step == "*" else [step] if step in data else []
        out = dict(data)
        for k in keys:
            if value is _DELETE and not rest:
                del out[k]
            else:
                out[k] = _inject(data[k], rest, value)
        return out
    if isinstance(data, list):
        out = list(data)
        idx = range(len(data)) if step == "*" else [step] if step < len(data) else []
        for i in idx:
            out[i] = _inject(data[i], rest, None if value is _DELETE else value)
        return out
    return data


def _song_example():
    from synthlab.studio import song_model as sm

    song = sm.new_song(120.0, "A1", "minor")
    d = sm.add_track(song, "drums", "Drums", drum_params={"kick": {"decay": 0.5}})
    s = sm.add_track(song, "synth", "Bass", curves={"cutoff": [[0.0, 0.2]]})
    a = sm.add_track(song, "audio", "Vox")
    dp = sm.add_pattern(
        song, "drums", "Beat", {"drums": [["kick", 0]], "drum_vel": {"0": 0.5}}
    )
    np_ = sm.add_pattern(
        song, "notes", "Riff", {"cells": [["A1", 0]], "velocities": {"0": 0.7}}
    )
    song["patterns"][np_]["slides"] = [0]
    sec = sm.add_section(song, "Verse", 4)
    sm.set_cell(song, sec, d, dp)
    sm.set_cell(song, sec, s, np_)
    song["sections"][0]["audio"].append(
        {"track_id": a, "clip": "take", "at_bar": 1, "gain": 0.9}
    )
    # automation lanes on both cells, so the sweeps reach their nodes
    sm.set_lane(song, sec, s, "cutoff", [[0.0, 0.2], [0.5, 0.9], [1.0, 0.4]])
    sm.set_lane(song, sec, d, "gain", [[0.0, 0.0], [1.0, 0.5]])
    return song


def _payload_example() -> dict:
    """`_example(PAYLOAD)`, with a real song: a generic song example is not
    a fixed point (its one id names a drums track, a drums pattern *and* a
    take's audio track, so the SONG post-hook drops the take)."""
    ex = _example(N.PAYLOAD)
    ex["song"] = _song_example()
    ex["baselines"]["song"]["data"]["state.song"] = _song_example()
    return ex


EXAMPLE = _payload_example()
PATHS = [p for p, _ in _paths(N.PAYLOAD) if p]


def test_example_is_valid_and_a_fixed_point():
    assert schema.conform_strict(EXAMPLE, N.PAYLOAD) == EXAMPLE
    assert EXAMPLE["song"]["sections"] and EXAMPLE["baselines"]["song"]
    assert "scenes" not in EXAMPLE  # v3: the song model replaced them


def test_schema_covers_every_persisted_key(tmp_path):
    s = StudioState(tmp_path)
    # (a key absent when unset is in no fresh session's file: Perform's launcher)
    optional = set(schema.PAYLOAD_OPTIONAL)
    assert set(N.PAYLOAD.fields) - optional == set(s._payload()) - {"v"}
    assert set(N.ARRANGEMENT.fields) == {f.name for f in fields(Arrangement)}
    for _label, keys in RESET_SCOPES.values():
        for k in keys:
            assert k in N.SCOPES
    assert set(N.BASELINES.fields) == set(RESET_SCOPES)
    assert len(PATHS) > 150  # the sweep below really walks the whole tree


def test_schema_defaults_match_the_live_defaults(tmp_path):
    s = StudioState(tmp_path)
    fresh = s._payload()
    for name, node in N.PAYLOAD.fields.items():
        if name in ("baselines", "arrangement", "patch"):
            continue
        if name in schema.PAYLOAD_OPTIONAL:  # absent when unset: unset is its default
            assert name not in fresh and node.default() == getattr(s, name), name
            continue
        assert node.default() == fresh[name], name
    assert Arrangement(**N.ARRANGEMENT.default()) == Arrangement()
    assert N.PATCH.default() == Patch().to_json()


def test_arrangement_clip_name_uses_the_clip_stores_own_fallback():
    """`_clip_name` (the schema node backing `arrangement.clips[].clip`)
    must normalize a degenerate lane clip name to "clip" — the same
    fallback `save_clip` uses. On the shared `safe_name` ("untitled"),
    which runs on startup/import/scenes/baselines, it would point a
    cleaned arrangement at a clip file that doesn't exist under that
    name. `_clip_name` must go through `clips.clip_safe`, the one clip
    sanitizer, not a different fallback of the same underlying function."""
    from synthlab.studio import clips

    out = schema.conform(
        {"clips": [{"clip": "...", "at_bar": 0, "gain": 1.0}]}, N.ARRANGEMENT
    )
    assert out["clips"][0]["clip"] == clips.clip_safe("...") == "clip"


@pytest.mark.parametrize("raw", ["...", "CON", "", "   "])
def test_arrangement_conform_matches_clip_safe_for_degenerate_names(raw):
    """Every degenerate raw clip name conforms through the schema to
    exactly what `clips.clip_safe` would give it — not the shared
    `safe_name`'s "untitled" default."""
    from synthlab.studio import clips as clips_mod

    conformed = schema.conform(
        {"clips": [{"clip": raw, "at_bar": 0, "gain": 1.0}]}, N.ARRANGEMENT
    )
    assert conformed["clips"][0]["clip"] == clips_mod.clip_safe(raw)


def test_every_clip_name_site_uses_clip_safe_not_the_shared_default(tmp_path):
    """Guard against this regression recurring somewhere else: every site
    that turns a raw clip name into a stored/looked-up name — clips.py
    itself, schema.py's `_clip_name`, and the two routes that resolve a
    clip by name — must call `clips.clip_safe` (fallback "clip"), never
    the shared `files.safe_name` directly (fallback "untitled").

    The routes used to be checked by regex-scanning `server.py` for their
    `@app.post(...)` bodies; they live in `routes/clips.py` now, so they
    are checked through the real routes instead: a degenerate name ("CON",
    a reserved device name) must resolve to the clip saved under "clip",
    and never to one saved under "untitled"."""
    import inspect
    import re

    import numpy as np
    from fastapi.testclient import TestClient

    from synthlab.studio import clips as clips_mod
    from synthlab.studio import schema as schema_mod
    from synthlab.studio.routes import clips as clip_routes
    from synthlab.studio.server import create_app

    # clips.py: no function may call the shared safe_name() directly —
    # only clip_safe() itself is allowed to (it's the one place that
    # picks the "clip" fallback).
    clips_src = inspect.getsource(clips_mod)
    for name, fn in inspect.getmembers(clips_mod, inspect.isfunction):
        if fn.__module__ != clips_mod.__name__ or name == "clip_safe":
            continue
        assert "safe_name(" not in inspect.getsource(fn), (
            f"clips.{name} calls safe_name() directly instead of clip_safe()"
        )
    assert re.search(r"^def clip_safe\(", clips_src, re.M)

    # schema.py's _clip_name must go through clips.clip_safe
    assert "safe_name(" not in inspect.getsource(schema_mod._clip_name)
    assert "clip_safe(" in inspect.getsource(schema_mod._clip_name)

    # the clip routes' module never reaches for the shared default
    assert "safe_name(" not in inspect.getsource(clip_routes)

    # behaviourally, through the clip-by-name serve route (the one route
    # that places a clip by name)
    tone = np.sin(np.linspace(0, 200, 4000)).astype(np.float32) * 0.3
    for saved_as, reachable in (("clip", True), ("untitled", False)):
        data = tmp_path / saved_as
        clips_mod.save_clip(data, saved_as, tone)
        client = TestClient(create_app(data_dir=data))
        wav = client.get("/clip/CON.wav")
        if reachable:
            assert wav.status_code == 200
            assert wav.content == client.get(f"/clip/{saved_as}.wav").content
        else:  # safe_name's "untitled" is never what a clip route looks up
            assert wav.status_code == 404


def test_every_node_survives_every_hostile_value():
    """conform never raises, and what it returns is itself conformant (a
    fixed point) — i.e. has exactly the types and ranges the consumers
    were written against. A nested path is conformed through its own
    top-level field's node (the payload is a Record, so that is exactly
    what the full conform does for it); a top-level path through the
    whole payload, where a missing required key must reject it."""
    failures = []
    for path in PATHS:
        top = path[0]
        for hostile in HOSTILES:
            if len(path) == 1:
                node, data = N.PAYLOAD, _inject(EXAMPLE, path, hostile)
            else:
                node = N.PAYLOAD.fields[top]
                data = _inject(EXAMPLE[top], path[1:], hostile)
            try:
                if node is N.PAYLOAD:
                    out = schema.conform_strict(data, node)
                else:  # what the payload Record does with its field's Invalid
                    out = schema.conform(data, node)
            except schema.Invalid:
                # only a *missing* required top-level key may reject the payload
                if not (top in N.PAYLOAD.required and hostile is _DELETE):
                    failures.append((path, hostile, "rejected"))
                continue
            except Exception as err:  # noqa: BLE001 - the test's whole point
                failures.append((path, hostile, repr(err)))
                continue
            if schema.conform_strict(out, node) != out:
                failures.append((path, hostile, "not a fixed point"))
    assert not failures, failures[:10]


def test_every_node_survives_apply_revert_scene_and_layer(tmp_path):
    """The state-level half of the sweep: every hostile payload applied to
    a real StudioState, then every scope reverted, every scene loaded,
    every layer selected — and the session still conformant after each.
    (Rendering is test_project.py's fuzz; this one is breadth.)"""
    s = StudioState(tmp_path)
    failures = []
    for path in PATHS:
        for hostile in ([1], {"x": 1}, _DELETE):  # types: the sweep above does values
            data = _inject(EXAMPLE, path, hostile)
            try:
                s._apply_or_restore(data)
            except schema.Invalid:
                continue
            try:
                for scope in RESET_SCOPES:
                    s.revert(scope)
                for i in range(len(s.song["sections"])):
                    load_section(s, i)
                for i in range(len(s.layers)):
                    s.select_layer(i)
                payload = {k: v for k, v in s._payload().items() if k != "v"}
                json.dumps(payload)
                if schema.conform_strict(payload, N.PAYLOAD) != payload:
                    failures.append((path, hostile, "not conformant"))
            except Exception as err:  # noqa: BLE001
                failures.append((path, hostile, repr(err)))
    assert not failures, failures[:10]


# --- a legitimate session is untouched ---------------------------------------


def _rich_session(tmp_path) -> StudioState:
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, cutoff=1234.5, unison=3, name="kept")
    s.arrangement = replace(
        s.arrangement,
        bars=2,
        steps_per_bar=8,
        cells=[["A1", 0], ["E2", 12], ["C2", 3]],  # 12: hidden by a shrink, kept
        velocities={"0": 0.5, "3": 1.0},
        slides=[3],
        drums=[["kick", 0], ["hat", 2], ["snare", 4]],
        drum_vel={"2": 0.4},
        drum_params={"kick": {"decay": 0.5}},
        drum_mix={"hat": {"level": 0.8, "mute": False}},
        drum_samples={"snare": "my take"},
        clips=[{"clip": "take", "at_bar": 1, "gain": 0.9}],
    )
    s.curves = {"cutoff": [[0.0, 0.1], [1.0, 0.9]]}
    s.macros[1] = {
        "name": "Bright",
        "value": 0.3,
        "targets": [{"param": "cutoff", "amt": -0.5}],
    }
    s.fx_master = clean_chain([{"kind": "reverb", "params": {"mix": 0.3}}])
    s.xy_axes = ["cutoff", "resonance"]
    s.xy_gesture = [[0.0, 0.2, 0.3], [1.5, 0.4, 0.5]]
    s.breed_starred = ["a", "b"]
    s.slot_a = replace(s.patch, cutoff=800.0)
    s.select_layer(1)
    s.arrangement = replace(s.arrangement, cells=[["A2", 1]])
    s.select_layer(0)
    s.loop_to_section("verse")
    s.bank_save(2, "mine")
    s.mark_baselines(RESET_SCOPES, "saved")
    return s


def test_a_normal_session_round_trips_identically(tmp_path):
    s = _rich_session(tmp_path)
    s.save()
    first = (tmp_path / "state.json").read_bytes()
    again = StudioState(tmp_path)
    assert again._payload() == s._payload()
    again.save()
    assert (tmp_path / "state.json").read_bytes() == first
    assert not list(tmp_path.glob("state.json.corrupt-*"))


def test_a_normal_session_is_a_schema_fixed_point(tmp_path):
    payload = _rich_session(tmp_path)._payload()
    payload.pop("v")
    assert schema.conform_strict(payload, N.PAYLOAD) == payload


def test_every_shipped_patch_is_a_schema_fixed_point():
    from synthlab.studio.factory import FACTORY, get_factory
    from synthlab.studio.library import LIBRARY, get_library

    patches = [get_factory(e["name"]) for e in FACTORY]
    patches += [get_library(e["name"]) for e in LIBRARY]
    assert len(patches) > 50
    for p in patches:
        d = p.to_json()
        assert schema.conform_strict(d, N.PATCH) == d, p.name


# --- the generic nodes --------------------------------------------------------


@pytest.mark.parametrize("x", [True, "3", [3], float("nan"), float("inf"), 10**400])
def test_num_rejects_non_numbers(x):
    with pytest.raises(schema.Invalid):
        schema.Num(0, 10).conform(x)


@pytest.mark.parametrize("x", [[1], {"a": 1}, True, None, 2.5])
def test_choice_is_hashable_safe(x):
    with pytest.raises(schema.Invalid):
        schema.Choice(("a", 1, 2)).conform(x)


def test_choice_returns_the_canonical_option():
    assert schema.Choice((1, 2, 4)).conform(2.0) == 2
    assert type(schema.Choice((1, 2, 4)).conform(2.0)) is int


def test_listof_dedupes_and_caps_cheaply():
    node = schema.ListOf(schema.Num(0, 10, integer=True), max_len=3, unique=int)
    assert node.conform([1] * 1_000_000 + [2, 2, 3, 4, 5]) == [1, 2, 3]
    last = schema.ListOf(schema.Str(), max_len=2, unique=str, keep="last")
    assert last.conform(["a", "b", "c", "c"]) == ["b", "c"]


def test_conform_never_returns_the_input_container():
    src = {"cutoff": [[0.0, 0.5]]}
    out = schema.conform(src, N.CURVES)
    assert out == src
    out["cutoff"][0][0] = 0.9
    assert src["cutoff"][0][0] == 0.0


# --- the song model's nodes ---------------------------------------------------
# SONG is swept here from its own root, with an example built through
# song_model (a generic `_example` can't satisfy the SONG post-hook's
# cross-references).


def _song_roots():
    ex = _song_example()
    return [
        (N.SONG, ex),
        (N.TRACK, ex["tracks"][0]),
        (N.TRACK, ex["tracks"][1]),
        (N.TRACK, ex["tracks"][2]),
        (
            N.PATTERN_DRUMS,
            next(p for p in ex["patterns"].values() if p["kind"] == "drums"),
        ),
        (
            N.PATTERN_NOTES,
            next(p for p in ex["patterns"].values() if p["kind"] == "notes"),
        ),
        (N.SECTION, ex["sections"][0]),
    ]


def test_song_example_is_a_fixed_point():
    for node, ex in _song_roots():
        assert schema.conform_strict(ex, node) == ex


def test_song_nodes_survive_every_hostile_value():
    """The same sweep as the payload's, rooted at each song node: never an
    exception, and the result is always a fixed point."""
    failures = []
    for node, ex in _song_roots():
        for path in [p for p, _ in _paths(node) if p]:
            for hostile in HOSTILES:
                data = _inject(ex, path, hostile)
                try:
                    out = schema.conform(data, node)
                except Exception as err:  # noqa: BLE001 - the test's whole point
                    failures.append((node, path, hostile, repr(err)))
                    continue
                if schema.conform_strict(out, node) != out:
                    failures.append((node, path, hostile, "not a fixed point"))
    assert not failures, failures[:10]


@pytest.mark.parametrize("raw", HOSTILES)
def test_song_conform_of_garbage_is_the_empty_song(raw):
    out = schema.conform(None if raw is _DELETE else raw, N.SONG)
    assert schema.conform_strict(out, N.SONG) == out


def test_song_post_nulls_cells_on_missing_or_wrong_kind_patterns():
    ex = _song_example()
    d, s, _a = (t["id"] for t in ex["tracks"])
    dp = next(k for k, p in ex["patterns"].items() if p["kind"] == "drums")
    np_ = next(k for k, p in ex["patterns"].items() if p["kind"] == "notes")
    bad = copy.deepcopy(ex)
    bad["sections"][0]["cells"] = {d: np_, s: "gone0000"}
    out = schema.conform(bad, N.SONG)
    assert out["sections"][0]["cells"] == {d: None, s: None}
    bad["sections"][0]["cells"] = {d: dp, s: dp}
    out = schema.conform(bad, N.SONG)
    assert out["sections"][0]["cells"] == {d: dp, s: None}


def test_song_post_drops_cells_on_missing_or_audio_tracks():
    ex = _song_example()
    d, _s, a = (t["id"] for t in ex["tracks"])
    dp = next(k for k, p in ex["patterns"].items() if p["kind"] == "drums")
    bad = copy.deepcopy(ex)
    bad["sections"][0]["cells"] = {d: dp, a: dp, "nosuch00": dp}
    out = schema.conform(bad, N.SONG)
    assert out["sections"][0]["cells"] == {d: dp}


def test_song_post_drops_takes_on_missing_or_non_audio_tracks():
    ex = _song_example()
    d, _s, a = (t["id"] for t in ex["tracks"])
    bad = copy.deepcopy(ex)
    take = bad["sections"][0]["audio"][0]
    bad["sections"][0]["audio"] = [
        take,
        dict(take, track_id="nosuch00"),
        dict(take, track_id=d),
    ]
    out = schema.conform(bad, N.SONG)
    assert [t["track_id"] for t in out["sections"][0]["audio"]] == [a]


def test_song_post_raises_section_bars_to_fit_its_longest_pattern():
    ex = _song_example()
    np_ = next(k for k, p in ex["patterns"].items() if p["kind"] == "notes")
    bad = copy.deepcopy(ex)
    bad["patterns"][np_]["bars"] = 3
    bad["sections"][0]["bars"] = 1
    out = schema.conform(bad, N.SONG)
    assert out["sections"][0]["bars"] == 3


def test_song_post_pins_pattern_ids_to_their_pool_keys():
    ex = _song_example()
    pid = next(iter(ex["patterns"]))
    bad = copy.deepcopy(ex)
    bad["patterns"][pid]["id"] = "other000"
    out = schema.conform(bad, N.SONG)
    assert out["patterns"][pid]["id"] == pid


def test_song_caps_tracks_per_kind_and_ids_are_unique():
    from synthlab.studio import limits

    ex = _song_example()
    synth = next(t for t in ex["tracks"] if t["kind"] == "synth")
    drums = next(t for t in ex["tracks"] if t["kind"] == "drums")
    bad = copy.deepcopy(ex)
    bad["tracks"] += [dict(synth, id=f"s{i:07d}") for i in range(10)]
    bad["tracks"] += [dict(drums, id="d0000001"), dict(synth)]  # dup id
    out = schema.conform(bad, N.SONG)
    kinds = [t["kind"] for t in out["tracks"]]
    assert kinds.count("synth") == limits.MAX_SYNTH_TRACKS
    assert kinds.count("drums") == limits.MAX_DRUM_TRACKS
    assert len(out["tracks"]) <= limits.MAX_TRACKS
    assert len({t["id"] for t in out["tracks"]}) == len(out["tracks"])


def test_song_ids_and_colors_are_markup_safe():
    ex = _song_example()
    bad = copy.deepcopy(ex)
    bad["tracks"][0]["id"] = '"><script>'
    bad["sections"][0]["color"] = "red;background:url(x)"
    out = schema.conform(bad, N.SONG)
    assert all(t["id"] != '"><script>' for t in out["tracks"])
    assert out["sections"][0]["color"].startswith("#")
    assert len(out["sections"][0]["color"]) == 7


def test_song_caps_sections_patterns_bars_and_repeats():
    from synthlab.studio import limits

    ex = _song_example()
    bad = copy.deepcopy(ex)
    sec = bad["sections"][0]
    sec["bars"], sec["repeats"] = 10_000, 10_000
    bad["sections"] += [dict(sec, id=f"x{i:07d}") for i in range(100)]
    pat = next(iter(bad["patterns"].values()))
    bad["patterns"] |= {f"p{i:07d}": dict(pat) for i in range(300)}
    out = schema.conform(bad, N.SONG)
    assert len(out["sections"]) == limits.MAX_SECTIONS
    assert len(out["patterns"]) == limits.MAX_PATTERNS
    assert out["sections"][0]["bars"] == limits.MAX_SECTION_BARS
    assert out["sections"][0]["repeats"] == limits.MAX_SECTION_REPEATS
