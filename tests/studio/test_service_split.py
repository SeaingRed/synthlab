"""The split of `synthlab/studio/service.py` into the `service` package moved
code and changed nothing else. What is pinned here was captured from the
one-file module BEFORE anything moved (`tests/fixtures/service_split/`):

- every name importable from `synthlab.studio.service`, and every attribute
  and method of `StudioService` with its signature;
- the region table as data: each region's template, sections, pages, watch,
  context and `inside`, in send order, and the lean-context sets.

And what keeps the units apart now: which module of the package may touch
which field of the service (`OWNERS`), each unit declaring the fields it
reaches, and a package module that holds the constructor and nothing else.

To re-capture after a deliberate change to the service's surface:
`uv run python tests/studio/test_service_split.py`.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient

import synthlab.studio.service as service_mod
from synthlab.studio.app import create_app
from synthlab.studio.events import QUIET
from synthlab.studio.service import StudioService

FIXTURES = Path(__file__).parent.parent / "fixtures" / "service_split"


# --- the public surface ------------------------------------------------------------


def _default(v) -> str:
    """A parameter's default as text that is the same in every process:
    a set in order, the quiet task by name (its repr holds an address)."""
    if v is QUIET:
        return "QUIET"
    if isinstance(v, frozenset | set):
        return f"{type(v).__name__}({sorted(v)})"
    return repr(v)


def _sig(obj) -> str | None:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = [
        p.replace(default=_Text(_default(p.default))) if p.default is not p.empty else p
        for p in sig.parameters.values()
    ]
    return str(sig.replace(parameters=params))


class _Text(str):
    """A default already rendered: its repr is the text itself."""

    def __repr__(self) -> str:
        return str(self)


def _ours(v) -> bool:
    """A function defined by the service module (not one it imports)."""
    home = getattr(v, "__module__", "") or ""
    return inspect.isfunction(v) and home.startswith("synthlab.studio.service")


def _name(fn) -> str | None:
    return getattr(fn, "__name__", None) if fn is not None else None


def surface(data_dir: Path) -> dict:
    """Every non-dunder name of the module (functions with signatures), of
    the class (same), of a fresh instance, and the region table."""
    mod = {
        n: _sig(v) if _ours(v) else None
        for n, v in vars(service_mod).items()
        if not n.startswith("__")
    }
    cls = {
        n: _sig(getattr(StudioService, n))
        if callable(getattr(StudioService, n))
        else type(inspect.getattr_static(StudioService, n)).__name__
        for n in dir(StudioService)
        if not n.startswith("__")
    }
    svc = create_app(data_dir=data_dir).state.svc
    sets = ("SECTIONS", "SONG_NEEDS", "ALL", "BANK_NEEDS", "DRUM_PANELS_NEEDS")
    return {
        "module": dict(sorted(mod.items())),
        "class": dict(sorted(cls.items())),
        "instance": sorted(vars(svc)),
        "init": _sig(StudioService),
        "regions": [
            [
                name,
                r.template,
                sorted(r.needs),
                sorted(r.pages),
                _name(r.watch),
                _name(r.context),
                r.inside,
            ]
            for name, r in service_mod.REGIONS.items()
        ],
        "page_regions": {v: list(n) for v, n in service_mod.PAGE_REGIONS.items()},
        "constants": {
            **{n: sorted(getattr(service_mod, n)) for n in (*sets, "BAR_NEEDS")},
            "ALL_VIEWS": sorted(service_mod.ALL_VIEWS),
            "PAD_VOICES": list(service_mod.PAD_VOICES),
            "DXY_AXES": [list(a) for a in service_mod.DXY_AXES],
            "POSTS": sorted(StudioService.POSTS),
            "AUDITION_VIEW": StudioService.AUDITION_VIEW,
        },
    }


def test_everything_importable_from_service_still_is(tmp_path):
    was = json.loads((FIXTURES / "surface.json").read_text(encoding="utf-8"))
    now = surface(tmp_path)
    for part in ("module", "class"):
        missing = sorted(set(was[part]) - set(now[part]))
        assert not missing, f"{part} names gone from synthlab.studio.service: {missing}"
        moved = {
            n: (sig, now[part][n])
            for n, sig in was[part].items()
            if now[part][n] != sig
        }
        assert not moved, f"{part} signatures changed: {moved}"
    assert not sorted(set(was["instance"]) - set(now["instance"]))
    assert now["init"] == was["init"]
    assert StudioService.__module__ == "synthlab.studio.service"
    assert service_mod.Region.__module__.startswith("synthlab.studio.service")


def test_the_region_table_is_the_one_the_one_file_module_had(tmp_path):
    """Send order is table order, and a watch or a context swapped for
    another would still import: the table is pinned as data."""
    was = json.loads((FIXTURES / "surface.json").read_text(encoding="utf-8"))
    now = surface(tmp_path)
    for part in ("regions", "page_regions", "constants"):
        assert now[part] == was[part], part


# --- who touches which field ---------------------------------------------------------

PACKAGE = Path(service_mod.__file__).parent
# every module but the package's own (the constructor, which declares them all)
UNIT_FILES = sorted(p for p in PACKAGE.glob("*.py") if p.stem != "__init__")


def _service_attrs(path: Path) -> set[str]:
    """Every `self.<name>` a module reads or writes — and `svc.<name>`: a
    watch or a lean context is handed the service under that name."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("self", "svc")
    }


def test_the_services_fields_are_touched_only_by_the_modules_that_own_them():
    """The split's point: the loop cache sat next to the song's memo and the
    per-request suppressed set, all reachable from 1,400 lines of one class.
    Each field names the modules allowed to touch it; a new use anywhere
    else fails here, where the reason is written down."""
    used = {p.stem: _service_attrs(p) for p in UNIT_FILES}
    for field, allowed in service_mod.OWNERS.items():
        touched = {m for m, attrs in used.items() if field in attrs}
        assert touched == set(allowed), (
            f"self.{field} is used in {sorted(touched)}; OWNERS "
            f"(service/__init__.py) says {sorted(allowed)}"
        )


def test_every_instance_field_has_an_owner(tmp_path):
    fields = set(vars(create_app(data_dir=tmp_path).state.svc))
    assert fields == set(service_mod.OWNERS), fields ^ set(service_mod.OWNERS)


def test_the_package_module_holds_the_constructor_and_nothing_else():
    """`__init__.py` declares the fields; a method added there would touch
    them outside every unit (and outside the OWNERS check above)."""
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert [c.name for c in classes] == ["StudioService"]
    methods = [n.name for n in classes[0].body if isinstance(n, ast.FunctionDef)]
    assert methods == ["__init__"]
    assert not [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    # beside the class: imports (the re-exports), the logger and OWNERS
    assigned = [
        t.id
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
    ]
    assert assigned == ["log", "OWNERS"]


def test_each_unit_declares_the_fields_it_reaches(tmp_path):
    """A unit's interface onto the service is written at the top of its
    class (bare annotations): exactly the instance fields its code touches.
    Reaching for one more is a deliberate, visible line. `regions` has no
    class: its watches are handed the service, and reach only `state`."""
    fields = set(vars(create_app(data_dir=tmp_path).state.svc))
    units = [b for b in StudioService.__mro__[1:] if b is not object]
    assert {u.__module__.rsplit(".", 1)[1] for u in units} == {
        p.stem for p in UNIT_FILES
    } - {"regions"}
    for unit in units:
        path = PACKAGE / (unit.__module__.rsplit(".", 1)[1] + ".py")
        reached = _service_attrs(path) & fields
        declared = set(vars(unit).get("__annotations__", {}))
        if not declared:  # 3.14: annotations are lazy
            declared = set(inspect.get_annotations(unit))
        # bare annotations only: an annotated class constant (POSTS) has a value
        declared -= set(vars(unit))
        assert declared == reached, (
            f"{unit.__name__}: declared but unused {sorted(declared - reached)}, "
            f"used but undeclared {sorted(reached - declared)}"
        )
    assert _service_attrs(PACKAGE / "regions.py") & fields == {"state"}


def test_a_name_patched_on_the_package_is_the_one_the_units_call(tmp_path, monkeypatch):
    """Tests replace these four as `synthlab.studio.service.<name>`
    (test_routes.py, test_concurrency.py, test_song_export.py, ...). The
    units read them off the package at call time, so a replacement there is
    what runs."""
    calls: list[str] = []

    def spy(name):
        real = getattr(service_mod, name)
        assert real.__module__ in ("synthlab.studio.render", "synthlab.studio.library")

        def wrapped(*a, **k):
            calls.append(name)
            return real(*a, **k)

        monkeypatch.setattr(service_mod, name, wrapped)

    for name in (
        "render_loop",
        "section_key",
        "section_master_audio",
        "library_by_family",
    ):
        spy(name)
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    svc.state.loop_to_section("Verse")
    svc.render("editor")
    svc.song_plan(*svc.song_snapshot())
    assert client.get("/view/library").status_code == 200
    assert set(calls) == {
        "render_loop",
        "section_key",
        "section_master_audio",
        "library_by_family",
    }


if __name__ == "__main__":  # capture the fixture (see the module docstring)
    import tempfile

    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        (FIXTURES / "surface.json").write_text(
            json.dumps(surface(Path(d) / "a"), indent=1) + "\n", encoding="utf-8"
        )
    print(f"wrote {sorted(p.name for p in FIXTURES.iterdir())}")
