import numpy as np

from synthlab import PRESETS, Patch, Synth
from synthlab.analysis.features import FEATURE_NAMES, Features, extract
from synthlab.explore.space import SpaceStore


def _cheap(i: float = 0.0) -> Features:
    """A Features vector without rendering audio — the cap tests add
    thousands of rows and only care about the row count."""
    return Features(**{n: 0.1 + 0.001 * i for n in FEATURE_NAMES})


def _add_preset(store, name, parents=()):
    patch = PRESETS[name]
    audio = Synth(patch).note("A2", 0.5)
    return store.add(patch, extract(audio), parents=parents)


def test_add_and_rows_roundtrip(tmp_path):
    store = SpaceStore(tmp_path / "space.jsonl")
    rid = _add_preset(store, "bass_reese")
    rows = store.rows()
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["patch"]["name"] == "bass_reese"
    assert "voice_fx" not in rows[0]["patch"]
    assert Patch(**rows[0]["patch"]).cutoff == PRESETS["bass_reese"].cutoff


def test_get_and_lineage(tmp_path):
    store = SpaceStore(tmp_path / "space.jsonl")
    parent = _add_preset(store, "bass_reese")
    child = _add_preset(store, "bass_acid", parents=[parent])
    child_row = store.get(child)
    assert child_row is not None
    assert child_row["parents"] == [parent]
    assert store.get("nope") is None


def test_corrupt_trailing_line_is_skipped(tmp_path):
    path = tmp_path / "space.jsonl"
    store = SpaceStore(path)
    _add_preset(store, "bass_reese")
    with open(path, "a") as f:
        f.write('{"id": "trunca')  # simulated crash mid-write
    assert len(store.rows()) == 1  # not fatal, corrupt line dropped


def test_project_2d_shapes_and_degenerate_cases(tmp_path):
    store = SpaceStore(tmp_path / "space.jsonl")
    assert store.project_2d() == []
    _add_preset(store, "bass_reese")
    single = store.project_2d()
    assert len(single) == 1 and single[0]["x"] == 0.0 and single[0]["y"] == 0.0
    for name in ("bass_acid", "pad_warm", "pluck_glass"):
        _add_preset(store, name)
    pts = store.project_2d()
    assert len(pts) == 4
    xs = [p["x"] for p in pts]
    assert np.all(np.isfinite(xs))
    assert np.std(xs) > 0, "projection collapsed"


def test_get_uses_index_and_survives_adds(tmp_path):
    store = SpaceStore(tmp_path / "s.jsonl")
    a = _add_preset(store, "bass_reese")
    _add_preset(store, "bass_acid")
    row_a = store.get(a)
    assert row_a is not None
    assert row_a["patch"]["name"] == "bass_reese"
    c = _add_preset(store, "pad_warm")
    assert store.get(c) is not None  # index refreshed after add


def test_rows_skip_missing_id(tmp_path):
    store = SpaceStore(tmp_path / "space.jsonl")
    _add_preset(store, "bass_reese")
    with open(store.path, "a") as f:
        f.write('{"t": 0, "patch": {}, "features": {}, "parents": []}\n')
    assert len(store.rows()) == 1  # id-less row skipped, not fatal
    assert store.get("x") is None
    assert store.project_2d() != []  # doesn't raise


def test_project_2d_skips_old_schema_rows(tmp_path):
    store = SpaceStore(tmp_path / "s.jsonl")
    _add_preset(store, "bass_reese")
    _add_preset(store, "bass_acid")
    old_row = (
        '{"id": "old1", "t": 0, "patch": {}, '
        '"features": {"centroid": 1.0}, "parents": []}\n'
    )
    with open(store.path, "a") as f:
        f.write(old_row)
    pts = store.project_2d()
    assert len(pts) == 2  # old-schema row skipped, not fatal


def test_store_compacts_so_the_log_cannot_grow_forever(tmp_path):
    """space.jsonl was append-only with no cap: one real session had 592 rows
    (892 KB) and every /fragment/map re-read, re-parsed and re-SVD'd all of
    them. Compaction keeps the most recent MAX_ROWS and drops the rest."""
    from synthlab.explore.space import _COMPACT_SLACK, MAX_ROWS, SpaceStore

    store = SpaceStore(tmp_path / "space.jsonl")
    n = int(MAX_ROWS * _COMPACT_SLACK) + 5  # enough to trip compaction once
    ids = [store.add(Patch(), _cheap(i)) for i in range(n)]
    rows = store.rows()
    # bounded, not exact: compaction fires at the slack line and adds resume
    assert len(rows) < n, "compaction never fired — the log is still unbounded"
    assert len(rows) <= MAX_ROWS * _COMPACT_SLACK, f"{len(rows)} rows is unbounded"
    # the newest rows are the ones kept, and they stay addressable
    assert rows[-1]["id"] == ids[-1]
    assert store.get(ids[-1]) is not None


def test_project_2d_limit_caps_what_the_map_plots(tmp_path):
    from synthlab.explore.space import SpaceStore

    store = SpaceStore(tmp_path / "space.jsonl")
    for i in range(40):
        store.add(Patch(), _cheap(i))
    assert len(store.project_2d()) == 40
    capped = store.project_2d(limit=12)
    assert len(capped) == 12
    # a cap keeps the most recent points, not the oldest
    assert capped[-1]["id"] == store.rows()[-1]["id"]


def test_lookups_do_not_reread_the_log(tmp_path, monkeypatch):
    """One store serves the studio: `get` is a dict hit, not a re-read and
    re-parse of the whole file (79 ms at 2,400 rows), and `add` keeps the
    in-memory rows current without reading the file back."""
    store = SpaceStore(tmp_path / "space.jsonl")
    ids = [store.add(Patch(), _cheap(i)) for i in range(50)]
    assert store.get(ids[0]) is not None
    parses: list = []
    real = SpaceStore._parse
    monkeypatch.setattr(
        SpaceStore, "_parse", lambda self: parses.append(1) or real(self)
    )
    for tid in ids:
        assert store.get(tid)["id"] == tid
    new = store.add(Patch(), _cheap(99))
    assert store.get(new) is not None and store.rows()[-1]["id"] == new
    assert parses == []


def test_a_log_changed_on_disk_is_read_again(tmp_path):
    """The in-memory rows are keyed by the file's (mtime, size): a log
    rewritten underneath the store (another process, a restore) is re-read,
    never served stale."""
    path = tmp_path / "space.jsonl"
    store = SpaceStore(path)
    first = store.add(Patch(), _cheap(1))
    other = SpaceStore(path)  # e.g. a second process appending
    second = other.add(Patch(), _cheap(2))
    assert store.get(second) is not None
    path.write_text("", encoding="utf-8")
    assert store.get(first) is None and store.rows() == []
