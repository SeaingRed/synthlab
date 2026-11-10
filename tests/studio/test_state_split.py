"""The split of `synthlab/studio/state.py` into the `state` package moved
code and changed nothing else. Three pins (`tests/fixtures/state_split/`):

- the bytes of state.json for a rich session (layers with followers, a song
  with sections, lanes and a take, undo history, a "before last load" stash,
  a track-sound edit in progress), and that loading those bytes and saving
  again writes them back unchanged;
- every name importable from `synthlab.studio.state`, and every attribute
  and method of `StudioState` with its signature (`surface.json`, captured
  from the one-file module BEFORE anything moved, and not re-captured);
- which module of the package may touch which runtime field (`OWNERS`).

**The bytes were re-captured once, on purpose.** As captured
from the one-file module, `rich_state.json` was the session's *first*
save, and that was not in the schema's conformed form: its layers 1 and 2
came from `load_preset` (no `silent` / `seq` / `curves`), and its "before
last load" stack held this builder's hand-built FX units without their
default parameters and a layer with `follow` after its other keys. A load
conformed all of that, so the file changed on the first restart, and the
conformed form was pinned beside it as `rich_state.reloaded.json` — which
is how the split was proved to change neither. With the split merged, the
first save was fixed to *be* the conformed form (`Persistence._payload`,
`tests/studio/test_state_conformed.py`), so the bytes moved exactly this
far: **`rich_state.json` and `rich_state_mid_edit.json` are now
byte-identical to what their `.reloaded.json` twins held** (38 lines of
57 k: FX defaults, three layer keys, one key order), and the twins, now
equal to them, are gone. The old first saves are kept as
`legacy_first_save.json` / `legacy_first_save_mid_edit.json`: files users
already have, which must still load and be written conformed by the next
save (pinned in `test_state_conformed.py`).

To re-capture the bytes after a deliberate change to what a session
persists: `uv run python tests/studio/test_state_split.py` (add
`--surface` to re-capture `surface.json` too, which a change to what is
persisted never needs).
"""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest

import synthlab.studio.state as state_mod
from synthlab.studio import song_model as sm
from synthlab.studio.drumfactory import DRUM_FACTORY
from synthlab.studio.state import StudioState
from synthlab.synth import Patch

FIXTURES = Path(__file__).parent.parent / "fixtures" / "state_split"


# --- a rich session ----------------------------------------------------------------


def _lf(raw: bytes) -> bytes:
    """state.json is written in text mode (CRLF on Windows); the fixtures
    are kept LF so a checkout's line-ending setting cannot change them."""
    return raw.replace(b"\r\n", b"\n")


def _ids(monkeypatch) -> None:
    """The song's ids are random (`secrets.token_hex`): count instead."""
    n = itertools.count(1)
    monkeypatch.setattr(secrets, "token_hex", lambda k=4: f"{next(n):0{2 * k}x}")


def build(data_dir: Path) -> tuple[StudioState, bytes, bytes]:
    """(the session, state.json as saved, state.json saved mid track-edit)."""
    s = StudioState(data_dir)
    s.patch = Patch(name="lead", osc1="saw", cutoff=2500.0)
    s.arrangement = replace(
        s.arrangement,
        bars=2,
        steps_per_bar=8,
        cells=[["A1", 0], ["C2", 3], ["E2", 9]],
        velocities={"3": 0.6},
        slides=[3],
        drums=[["kick", 0], ["snare", 4], ["hat", 2]],
        drum_vel={"4": 0.7},
        clips=[{"clip": "jam", "at_bar": 0, "gain": 0.9}],
    )
    s.mark_baselines(("sound", "sequence", "pattern"), "start")
    s.push_history()
    s.patch = replace(s.patch, cutoff=900.0)
    # two followers: one selected (its own undo ring and stash), one not
    reverb = [{"kind": "reverb", "params": {"mix": 0.3}}]
    s.add_layer(Patch(name="sub", osc1="sine"), reverb, octave=-1, gain=0.8)
    s.stash_for_load()
    s.patch = replace(s.patch, resonance=0.4, name="sub 2")
    s.add_layer(Patch(name="top", osc1="square"), select=False, octave=1)
    s.set_layer_solo(2, True)
    s.set_layer_solo(2, False)
    s.select_layer(0)
    s.curves["cutoff"] = [[0.0, 0.2], [1.0, 0.8]]
    s.layers[0]["fx"] = [{"kind": "delay", "params": {"mix": 0.2}}]
    verse = s.loop_to_section("Verse")
    s.arrangement = replace(s.arrangement, cells=[["A1", 1], ["E2", 5]])
    chorus = s.loop_to_section("Chorus")
    # a whole Sound saved and loaded back: "before last load" holds a stack
    s.save_preset("stack", include_layers=True)
    s.patch = replace(s.patch, cutoff=400.0, name="dark")
    s.set_layer_muted(2, True)
    s.load_preset("stack")
    assert s.slot_prev is not None and s.stack_prev is not None
    s.select_layer(1)  # slot 1's own ring; slot 0's waits in the per-slot store
    s.push_history()
    s.patch = replace(s.patch, release=0.9)
    s.select_layer(0)
    # the drum side: a pattern undo step, a factory beat, a bank save
    s.push_pattern_undo()
    s.apply_pattern(DRUM_FACTORY[1])
    assert s.bank_save(3, "mine")
    song = json.loads(json.dumps(s.song))
    synth = next(t["id"] for t in song["tracks"] if t["kind"] == "synth")
    sm.set_lane(song, verse, synth, "gain", [[0.0, 0.3], [0.5, 1.0], [1.0, 0.6]])
    sm.set_lane(song, chorus, synth, "cutoff", [[0.0, 0.1], [1.0, 0.9]])
    s.song = state_mod.schema.conform(song, state_mod.schema.nodes().SONG)
    s.take_history.append({"take": "t1", "pattern_id": "x", "before": None})
    s.slot_a, s.slot_b, s.ab_live = s.patch, Patch(name="b", cutoff=700.0), "a"
    s.macros[0]["value"] = 0.25
    s.grid_span = 2
    s.save()
    plain = _lf(s._state_path.read_bytes())
    # mid track-edit the file keeps the loop's own sound, the song the track's
    assert s.edit_track(synth)
    s.push_history()
    s.patch = replace(s.patch, cutoff=5000.0, name="track sound")
    s.curves["resonance"] = [[0.0, 0.1], [1.0, 0.5]]
    assert s.mirror_patch_to_track()
    s.save_soon(60)
    s.flush()
    return s, plain, _lf(s._state_path.read_bytes())


def test_a_rich_session_saves_the_pinned_bytes(tmp_path, monkeypatch):
    """Its first save, and its save mid track-edit (the module docstring
    says how these bytes relate to the ones the one-file module wrote)."""
    _ids(monkeypatch)
    s, plain, mid_edit = build(tmp_path)
    assert plain == _lf((FIXTURES / "rich_state.json").read_bytes())
    assert mid_edit == _lf((FIXTURES / "rich_state_mid_edit.json").read_bytes())
    # the runtime half of the session did not leak into either
    assert s._history and s._layer_undo and s._pattern_undo and s.take_history
    assert s.end_track_edit()


def _reloaded(data_dir: Path, saved: bytes) -> StudioState:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "state.json").write_bytes(saved)
    s = StudioState(data_dir)
    s.save()
    return s


@pytest.mark.parametrize("name", ["rich_state", "rich_state_mid_edit"])
def test_the_saved_bytes_load_and_save_back_unchanged(tmp_path, name):
    """What is saved is what a load holds: a load and a save of the pinned
    bytes write them back, the first time as every time after."""
    saved = _lf((FIXTURES / f"{name}.json").read_bytes())
    s = _reloaded(tmp_path / "once", saved)
    assert _lf(s._state_path.read_bytes()) == saved
    assert s.project_payload() == {"format": "synthlab-project", **s._payload()}
    # a project export of it imports to the same bytes too
    other = StudioState(tmp_path / "other")
    other.load_project(json.loads(json.dumps(s.project_payload())))
    assert _lf(other._state_path.read_bytes()) == saved


# --- the public surface ------------------------------------------------------------


def _sig(obj) -> str | None:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return None


def _ours(v) -> bool:
    """A function defined by the state module (not one it imports)."""
    home = getattr(v, "__module__", "") or ""
    return inspect.isfunction(v) and home.startswith("synthlab.studio.state")


def surface(data_dir: Path) -> dict:
    """Every non-dunder name of the module (functions with signatures), of
    the class (same), and of a fresh instance."""
    mod = {
        n: _sig(v) if _ours(v) else None
        for n, v in vars(state_mod).items()
        if not n.startswith("__")
    }
    cls = {
        n: _sig(getattr(StudioState, n))
        if callable(getattr(StudioState, n))
        else type(inspect.getattr_static(StudioState, n)).__name__
        for n in dir(StudioState)
        if not n.startswith("__")
    }
    return {
        "module": dict(sorted(mod.items())),
        "class": dict(sorted(cls.items())),
        "instance": sorted(vars(StudioState(data_dir))),
        "init": _sig(StudioState),
    }


def test_everything_importable_from_state_still_is(tmp_path):
    was = json.loads((FIXTURES / "surface.json").read_text(encoding="utf-8"))
    now = surface(tmp_path)
    for part in ("module", "class"):
        missing = sorted(set(was[part]) - set(now[part]))
        assert not missing, f"{part} names gone from synthlab.studio.state: {missing}"
        moved = {
            n: (sig, now[part][n])
            for n, sig in was[part].items()
            if now[part][n] != sig
        }
        assert not moved, f"{part} signatures changed: {moved}"
    assert not sorted(set(was["instance"]) - set(now["instance"]))
    assert now["init"] == was["init"]
    assert StudioState.__module__ == "synthlab.studio.state"
    assert state_mod.Arrangement.__module__.startswith("synthlab.studio.state")


# --- who touches which field ---------------------------------------------------------

PACKAGE = Path(state_mod.__file__).parent
# every module but the package's own (the constructor, which declares them all)
UNIT_FILES = sorted(p for p in PACKAGE.glob("*.py") if p.stem != "__init__")


def _self_attrs(path: Path) -> set[str]:
    """Every `self.<name>` a module reads or writes."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }


def test_runtime_fields_are_touched_only_by_the_modules_that_own_them():
    """The split's point: a session-global field next to a per-slot one is
    a bug waiting (the "before last load" stash that was not the slot's;
    the save timer cleared by a failed write). Each runtime field names the
    modules allowed to touch it; a new use anywhere else fails here, where
    the reason is written down."""
    used = {p.stem: _self_attrs(p) for p in UNIT_FILES}
    for field, allowed in state_mod.OWNERS.items():
        touched = {m for m, attrs in used.items() if field in attrs}
        assert touched == set(allowed), (
            f"self.{field} is used in {sorted(touched)}; OWNERS "
            f"(state/__init__.py) says {sorted(allowed)}"
        )


def test_every_private_instance_field_has_an_owner(tmp_path):
    private = {n for n in vars(StudioState(tmp_path)) if n.startswith("_")}
    assert private <= set(state_mod.OWNERS), sorted(private - set(state_mod.OWNERS))


def test_the_package_module_holds_the_constructor_and_nothing_else():
    """`__init__.py` declares the fields; a method added there would touch
    them outside every unit (and outside the OWNERS check above)."""
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StudioState"
    )
    assert [n.name for n in cls.body if isinstance(n, ast.FunctionDef)] == ["__init__"]
    assert not [n for n in tree.body if isinstance(n, ast.FunctionDef)]


def test_each_unit_declares_the_fields_it_reaches(tmp_path):
    """A unit's interface onto the session is written at the top of its
    class (bare annotations): exactly the instance fields its code touches.
    Reaching for one more is a deliberate, visible line."""
    fields = set(vars(StudioState(tmp_path)))
    units = [b for b in StudioState.__mro__[1:] if b is not object]
    assert {u.__module__.rsplit(".", 1)[1] for u in units} == {
        p.stem for p in UNIT_FILES
    } - {"model"}
    for unit in units:
        path = PACKAGE / (unit.__module__.rsplit(".", 1)[1] + ".py")
        reached = _self_attrs(path) & fields
        declared = set(vars(unit).get("__annotations__", {}))
        if not declared:  # 3.14: annotations are lazy
            declared = set(inspect.get_annotations(unit))
        assert declared == reached, (
            f"{unit.__name__}: declared but unused {sorted(declared - reached)}, "
            f"used but undeclared {sorted(reached - declared)}"
        )


if __name__ == "__main__":  # capture the fixtures (see the module docstring)
    import sys
    import tempfile

    FIXTURES.mkdir(parents=True, exist_ok=True)
    n = itertools.count(1)
    secrets.token_hex = lambda k=4: f"{next(n):0{2 * k}x}"
    wrote = ["rich_state.json", "rich_state_mid_edit.json"]
    with tempfile.TemporaryDirectory() as d:
        _, plain, mid = build(Path(d) / "a")
        (FIXTURES / "rich_state.json").write_bytes(plain)
        (FIXTURES / "rich_state_mid_edit.json").write_bytes(mid)
        if "--surface" in sys.argv:
            (FIXTURES / "surface.json").write_text(
                json.dumps(surface(Path(d) / "b"), indent=1) + "\n", encoding="utf-8"
            )
            wrote.append("surface.json")
    print(f"wrote {wrote}")
